#!/usr/bin/env python

# Copyright 2012 GRNET S.A. All rights reserved.
#
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
#
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
#
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

import dialog
import sys
import os
import textwrap
import signal
import StringIO

from image_creator import __version__ as version
from image_creator.util import FatalError, MD5
from image_creator.output.dialog import GaugeOutput, InfoBoxOutput
from image_creator.disk import Disk
from image_creator.os_type import os_cls
from image_creator.kamaki_wrapper import Kamaki, ClientError
from image_creator.help import get_help_file

MSGBOX_WIDTH = 60
YESNO_WIDTH = 50
MENU_WIDTH = 70
INPUTBOX_WIDTH = 70
CHECKBOX_WIDTH = 70
HELP_WIDTH = 70

CONFIGURATION_TASKS = [
 ("Partition table manipulation", ["FixPartitionTable"],
  ["linux", "windows"]),
 ("File system resize",
  ["FilesystemResizeUnmounted", "FilesystemResizeMounted"],
  ["linux", "windows"]),
 ("Swap partition configuration", ["AddSwap"], ["linux"]),
 ("SSH keys removal", ["DeleteSSHKeys"], ["linux"]),
 ("Temporal RDP disabling", ["DisableRemoteDesktopConnections"], ["windows"]),
 ("SELinux relabeling at next boot", ["SELinuxAutorelabel"],
  ["linux"]),
 ("Hostname/Computer Name assignment", ["AssignHostname"],
  ["windows", "linux"]),
 ("Password change", ["ChangePassword"], ["windows", "linux"]),
 ("File injection", ["EnforcePersonality"], ["windows", "linux"])
]


class Reset(Exception):
    pass


def confirm_exit(d, msg=''):
    return not d.yesno("%s Do you want to exit?" % msg, width=YESNO_WIDTH)


def confirm_reset(d):
    return not d.yesno(
        "Are you sure you want to reset everything?",
        width=YESNO_WIDTH)


def update_background_title(session):
    d = session['dialog']
    dev = session['device']

    MB = 2 ** 20

    size = (dev.meta['SIZE'] + MB - 1) // MB
    title = "OS: %s, Distro: %s, Size: %dMB" % (dev.ostype, dev.distro, size)

    d.setBackgroundTitle(title)


def extract_image(session):
    d = session['dialog']
    dir = os.getcwd()
    while 1:
        if dir and dir[-1] != os.sep:
            dir = dir + os.sep

        (code, path) = d.fselect(dir, 10, 50, title="Save image as...")
        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            return False

        if os.path.isdir(path):
            dir = path
            continue

        if os.path.isdir("%s.meta" % path):
            d.msgbox("Can't overwrite directory `%s.meta'" % path,
                     width=MSGBOX_WIDTH)
            continue

        if os.path.isdir("%s.md5sum" % path):
            d.msgbox("Can't overwrite directory `%s.md5sum'" % path,
                     width=MSGBOX_WIDTH)
            continue

        basedir = os.path.dirname(path)
        name = os.path.basename(path)
        if not os.path.exists(basedir):
            d.msgbox("Directory `%s' does not exist" % basedir,
                     width=MSGBOX_WIDTH)
            continue

        dir = basedir
        if len(name) == 0:
            continue

        files = ["%s%s" % (path, ext) for ext in ('', '.meta', '.md5sum')]
        overwrite = filter(os.path.exists, files)

        if len(overwrite) > 0:
            if d.yesno("The following file(s) exist:\n"
                        "%s\nDo you want to overwrite them?" %
                        "\n".join(overwrite), width=YESNO_WIDTH):
                continue

        out = GaugeOutput(d, "Image Extraction", "Extracting image...")
        try:
            dev = session['device']
            if "checksum" not in session:
                size = dev.meta['SIZE']
                md5 = MD5(out)
                session['checksum'] = md5.compute(session['snapshot'], size)

            # Extract image file
            dev.out = out
            dev.dump(path)

            # Extract metadata file
            out.output("Extracting metadata file...")
            metastring = '\n'.join(
                ['%s=%s' % (k, v) for (k, v) in session['metadata'].items()])
            metastring += '\n'
            with open('%s.meta' % path, 'w') as f:
                f.write(metastring)
            out.success('done')

            # Extract md5sum file
            out.output("Extracting md5sum file...")
            md5str = "%s %s\n" % (session['checksum'], name)
            with open('%s.md5sum' % path, 'w') as f:
                f.write(md5str)
            out.success("done")

        finally:
            out.cleanup()
        d.msgbox("Image file `%s' was successfully extracted!" % path,
                 width=MSGBOX_WIDTH)
        break

    return True


def upload_image(session):
    d = session["dialog"]
    size = session['device'].meta['SIZE']

    if "account" not in session:
        d.msgbox("You need to provide your ~okeanos login username before you "
                 "can upload images to pithos+", width=MSGBOX_WIDTH)
        return False

    if "token" not in session:
        d.msgbox("You need to provide your ~okeanos account authentication "
                 "token before you can upload images to pithos+",
                 width=MSGBOX_WIDTH)
        return False

    while 1:
        init = session["upload"] if "upload" in session else ''
        (code, answer) = d.inputbox("Please provide a filename:", init=init,
                                    width=INPUTBOX_WIDTH)

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            return False

        filename = answer.strip()
        if len(filename) == 0:
            d.msgbox("Filename cannot be empty", width=MSGBOX_WIDTH)
            continue

        break

    out = GaugeOutput(d, "Image Upload", "Uploading...")
    if 'checksum' not in session:
        md5 = MD5(out)
        session['checksum'] = md5.compute(session['snapshot'], size)
    try:
        kamaki = Kamaki(session['account'], session['token'], out)
        try:
            # Upload image file
            with open(session['snapshot'], 'rb') as f:
                session["upload"] = kamaki.upload(f, size, filename,
                                                  "Calculating block hashes",
                                                  "Uploading missing blocks")
            # Upload metadata file
            out.output("Uploading metadata file...")
            metastring = '\n'.join(
                ['%s=%s' % (k, v) for (k, v) in session['metadata'].items()])
            metastring += '\n'
            kamaki.upload(StringIO.StringIO(metastring), size=len(metastring),
                          remote_path="%s.meta" % filename)
            out.success("done")

            # Upload md5sum file
            out.output("Uploading md5sum file...")
            md5str = "%s %s\n" % (session['checksum'], filename)
            kamaki.upload(StringIO.StringIO(md5str), size=len(md5str),
                          remote_path="%s.md5sum" % filename)
            out.success("done")

        except ClientError as e:
            d.msgbox("Error in pithos+ client: %s" % e.message,
                     title="Pithos+ Client Error", width=MSGBOX_WIDTH)
            if 'upload' in session:
                del session['upload']
            return False
    finally:
        out.cleanup()

    d.msgbox("Image file `%s' was successfully uploaded to pithos+" % filename,
             width=MSGBOX_WIDTH)

    return True


def register_image(session):
    d = session["dialog"]

    if "account" not in session:
        d.msgbox("You need to provide your ~okeanos login username before you "
                 "can register an images to cyclades",
                 width=MSGBOX_WIDTH)
        return False

    if "token" not in session:
        d.msgbox("You need to provide your ~okeanos account authentication "
                 "token before you can register an images to cyclades",
                 width=MSGBOX_WIDTH)
        return False

    if "upload" not in session:
        d.msgbox("You need to have an image uploaded to pithos+ before you "
                 "can register it to cyclades",
                 width=MSGBOX_WIDTH)
        return False

    while 1:
        (code, answer) = d.inputbox("Please provide a registration name:"
                                " be registered:", width=INPUTBOX_WIDTH)
        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            return False

        name = answer.strip()
        if len(name) == 0:
            d.msgbox("Registration name cannot be empty", width=MSGBOX_WIDTH)
            continue
        break

    out = GaugeOutput(d, "Image Registration", "Registrating image...")
    try:
        out.output("Registring image to cyclades...")
        try:
            kamaki = Kamaki(session['account'], session['token'], out)
            kamaki.register(name, session['upload'], session['metadata'])
            out.success('done')
        except ClientError as e:
            d.msgbox("Error in pithos+ client: %s" % e.message)
            return False
    finally:
        out.cleanup()

    d.msgbox("Image `%s' was successfully registered to cyclades as `%s'" %
             (session['upload'], name), width=MSGBOX_WIDTH)
    return True


def kamaki_menu(session):
    d = session['dialog']
    default_item = "Account"
    while 1:
        account = session["account"] if "account" in session else "<none>"
        token = session["token"] if "token" in session else "<none>"
        upload = session["upload"] if "upload" in session else "<none>"

        choices = [("Account", "Change your ~okeanos username: %s" % account),
                   ("Token", "Change your ~okeanos token: %s" % token),
                   ("Upload", "Upload image to pithos+"),
                   ("Register", "Register image to cyclades: %s" % upload)]

        (code, choice) = d.menu(
            text="Choose one of the following or press <Back> to go back.",
            width=MENU_WIDTH, choices=choices, cancel="Back", help_button=1,
            default_item=default_item, title="Image Registration Menu")

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            return False

        if choice == "Account":
            default_item = "Account"
            (code, answer) = d.inputbox(
                "Please provide your ~okeanos account e-mail address:",
                init=session["account"] if "account" in session else '',
                width=70)
            if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
                continue
            if len(answer) == 0 and "account" in session:
                    del session["account"]
            else:
                session["account"] = answer.strip()
                default_item = "Token"
        elif choice == "Token":
            default_item = "Token"
            (code, answer) = d.inputbox(
                "Please provide your ~okeanos account authetication token:",
                init=session["token"] if "token" in session else '',
                width=70)
            if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
                continue
            if len(answer) == 0 and "token" in session:
                del session["token"]
            else:
                session["token"] = answer.strip()
                default_item = "Upload"
        elif choice == "Upload":
            if upload_image(session):
                default_item = "Register"
            else:
                default_item = "Upload"
        elif choice == "Register":
            if register_image(session):
                return True
            else:
                default_item = "Register"


def add_property(session):
    d = session['dialog']

    while 1:
        (code, answer) = d.inputbox("Please provide a name for a new image"
                                    " property:", width=INPUTBOX_WIDTH)
        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            return False

        name = answer.strip()
        if len(name) == 0:
            d.msgbox("A property name cannot be empty", width=MSGBOX_WIDTH)
            continue

        break

    while 1:
        (code, answer) = d.inputbox("Please provide a value for image "
                                   "property %s" % name, width=INPUTBOX_WIDTH)
        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            return False

        value = answer.strip()
        if len(value) == 0:
            d.msgbox("Value cannot be empty", width=MSGBOX_WIDTH)
            continue

        break

    session['metadata'][name] = value

    return True


def modify_properties(session):
    d = session['dialog']

    while 1:
        choices = []
        for (key, val) in session['metadata'].items():
            choices.append((str(key), str(val)))

        (code, choice) = d.menu(
            "In this menu you can edit existing image properties or add new "
            "ones. Be carefull! Most properties have special meaning and "
            "alter the image deployment behaviour. Press <HELP> to see more "
            "information about image properties. Press <BACK> when done.",
            height=18, width=MENU_WIDTH, choices=choices, menu_height=10,
            ok_label="Edit", extra_button=1, extra_label="Add", cancel="Back",
            help_button=1, title="Image Metadata")

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            break
        # Edit button
        elif code == d.DIALOG_OK:
            (code, answer) = d.inputbox("Please provide a new value for "
                    "the image property with name `%s':" % choice,
                    init=session['metadata'][choice], width=INPUTBOX_WIDTH)
            if code not in (d.DIALOG_CANCEL, d.DIALOG_ESC):
                value = answer.strip()
                if len(value) == 0:
                    d.msgbox("Value cannot be empty!")
                    continue
                else:
                    session['metadata'][choice] = value
        # ADD button
        elif code == d.DIALOG_EXTRA:
            add_property(session)


def delete_properties(session):
    d = session['dialog']

    choices = []
    for (key, val) in session['metadata'].items():
        choices.append((key, "%s" % val, 0))

    (code, to_delete) = d.checklist("Choose which properties to delete:",
                                    choices=choices, width=CHECKBOX_WIDTH)

    # If the user exits with ESC or CANCEL, the returned tag list is empty.
    for i in to_delete:
        del session['metadata'][i]

    cnt = len(to_delete)
    if cnt > 0:
        d.msgbox("%d image properties were deleted." % cnt, width=MSGBOX_WIDTH)


def exclude_tasks(session):
    d = session['dialog']

    index = 0
    displayed_index = 1
    choices = []
    mapping = {}
    if 'excluded_tasks' not in session:
        session['excluded_tasks'] = []

    if -1 in session['excluded_tasks']:
        if not d.yesno("Image deployment configuration is disabled. "
                       "Do you wish to enable it?", width=YESNO_WIDTH):
            session['excluded_tasks'].remove(-1)
        else:
            return

    for (msg, task, osfamily) in CONFIGURATION_TASKS:
        if session['metadata']['OSFAMILY'] in osfamily:
            checked = 1 if index in session['excluded_tasks'] else 0
            choices.append((str(displayed_index), msg, checked))
            mapping[displayed_index] = index
            displayed_index += 1
        index += 1

    while 1:
        (code, tags) = d.checklist(
            text="Please choose which configuration tasks you would like to "
                 "prevent from running during image deployment. "
                 "Press <No Config> to supress any configuration. "
                 "Press <Help> for more help on the image deployment "
                 "configuration tasks.",
            choices=choices, height=19, list_height=8, width=CHECKBOX_WIDTH,
            help_button=1, extra_button=1, extra_label="No Config",
            title="Exclude Configuration Tasks")

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            break
        elif code == d.DIALOG_HELP:
            help_file = get_help_file("configuration_tasks")
            assert os.path.exists(help_file)
            d.textbox(help_file, title="Configuration Tasks",
                      width=70, height=40)
        # No Config button
        elif code == d.DIALOG_EXTRA:
            session['excluded_tasks'] = [-1]
            session['task_metadata'] = ["EXCLUDE_ALL_TASKS"]
            break
        elif code == d.DIALOG_OK:
            session['excluded_tasks'] = []
            for tag in tags:
                session['excluded_tasks'].append(mapping[int(tag)])

            exclude_metadata = []
            for task in session['excluded_tasks']:
                exclude_metadata.extend(CONFIGURATION_TASKS[task][1])

            session['task_metadata'] = \
                        map(lambda x: "EXCLUDE_TASK_%s" % x, exclude_metadata)
            break


def sysprep(session):
    d = session['dialog']
    image_os = session['image_os']

    wrapper = textwrap.TextWrapper(width=65)

    help_title = "System Preperation Tasks"
    sysprep_help = "%s\n%s\n\n" % (help_title, '=' * len(help_title))

    if 'exec_syspreps' not in session:
        session['exec_syspreps'] = []

    all_syspreps = image_os.list_syspreps()
    # Only give the user the choice between syspreps that have not ran yet
    syspreps = [s for s in all_syspreps if s not in session['exec_syspreps']]

    while 1:
        choices = []
        index = 0
        for sysprep in syspreps:
            name, descr = image_os.sysprep_info(sysprep)
            display_name = name.replace('-', ' ').capitalize()
            sysprep_help += "%s\n" % display_name
            sysprep_help += "%s\n" % ('-' * len(display_name))
            sysprep_help += "%s\n\n" % wrapper.fill(" ".join(descr.split()))
            enabled = 1 if sysprep.enabled else 0
            choices.append((str(index + 1), display_name, enabled))
            index += 1

        (code, tags) = d.checklist(
            "Please choose which system preperation tasks you would like to "
            "run on the image. Press <Help> to see details about the system "
            "preperation tasks.", title="Run system preperation tasks",
            choices=choices, width=70, ok_label="Run", help_button=1)

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            break
        elif code == d.DIALOG_HELP:
            d.scrollbox(sysprep_help, width=HELP_WIDTH)
        elif code == d.DIALOG_OK:
            # Enable selected syspreps and disable the rest
            for i in range(len(syspreps)):
                if str(i + 1) in tags:
                    image_os.enable_sysprep(syspreps[i])
                    session['exec_syspreps'].append(syspreps[i])
                else:
                    image_os.disable_sysprep(syspreps[i])

            out = InfoBoxOutput(d, "Image Configuration")
            try:
                dev = session['device']
                dev.out = out
                dev.mount(readonly=False)
                try:
                    # The checksum is invalid. We have mounted the image rw
                    if 'checksum' in session:
                        del session['checksum']

                    image_os.out = out
                    image_os.do_sysprep()

                    # Disable syspreps that have ran
                    for sysprep in session['exec_syspreps']:
                        image_os.disable_sysprep(sysprep)

                    image_os.out.finalize()
                finally:
                    dev.umount()
            finally:
                out.cleanup()
            break


def customize_menu(session):
    d = session['dialog']

    choices = [("Sysprep", "Run various image preperation tasks"),
               ("Shrink", "Shrink image"),
               ("View/Modify", "View/Modify image properties"),
               ("Delete", "Delete image properties"),
               ("Exclude", "Exclude various deployment tasks from running")]

    default_item = "Sysprep"

    actions = {"Sysprep": sysprep,
               "View/Modify": modify_properties,
               "Delete": delete_properties,
               "Exclude": exclude_tasks}
    while 1:
        (code, choice) = d.menu(
            text="Choose one of the following or press <Back> to exit.",
            width=MENU_WIDTH, choices=choices, cancel="Back", height=13,
            menu_height=len(choices), default_item=default_item,
            title="Image Customization Menu")

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            break
        elif choice in actions:
            default_item = choice
            actions[choice](session)


def main_menu(session):
    d = session['dialog']
    dev = session['device']

    update_background_title(session)

    choices = [("Customize", "Customize image & ~okeanos deployment options"),
               ("Register", "Register image to ~okeanos"),
               ("Extract", "Dump image to local file system"),
               ("Reset", "Reset everything and start over again"),
               ("Help", "Get help for using snf-image-creator")]

    default_item = "Customize"

    actions = {"Customize": customize_menu, "Register": kamaki_menu,
               "Extract": extract_image}
    while 1:
        (code, choice) = d.menu(
            text="Choose one of the following or press <Exit> to exit.",
            width=MENU_WIDTH, choices=choices, cancel="Exit", height=13,
            default_item=default_item, menu_height=len(choices),
            title="Image Creator for ~okeanos (snf-image-creator version %s)" %
                  version)

        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            if confirm_exit(d):
                break
        elif choice == "Reset":
            if confirm_reset(d):
                d.infobox("Resetting snf-image-creator. Please wait...")
                raise Reset
        elif choice in actions:
            actions[choice](session)


def select_file(d, media):
    root = os.sep
    while 1:
        if media is not None:
            if not os.path.exists(media):
                d.msgbox("The file you choose does not exist",
                         width=MSGBOX_WIDTH)
            else:
                break

        (code, media) = d.fselect(root, 10, 50,
                                 title="Please select input media")
        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            if confirm_exit(d, "You canceled the media selection dialog box."):
                sys.exit(0)
            else:
                media = None
                continue

    return media


def image_creator(d):
    basename = os.path.basename(sys.argv[0])
    usage = "Usage: %s [input_media]" % basename
    if len(sys.argv) > 2:
        sys.stderr.write("%s\n" % usage)
        return 1

    if os.geteuid() != 0:
        raise FatalError("You must run %s as root" % basename)

    media = select_file(d, sys.argv[1] if len(sys.argv) == 2 else None)

    d.setBackgroundTitle('snf-image-creator')

    out = GaugeOutput(d, "Initialization", "Initializing...")
    disk = Disk(media, out)

    def signal_handler(signum, frame):
        out.cleanup()
        disk.cleanup()

    signal.signal(signal.SIGINT, signal_handler)
    try:
        snapshot = disk.snapshot()
        dev = disk.get_device(snapshot)

        out.output("Collecting image metadata...")
        metadata = dev.meta
        dev.mount(readonly=True)
        cls = os_cls(dev.distro, dev.ostype)
        image_os = cls(dev.root, dev.g, out)
        dev.umount()
        metadata.update(image_os.meta)
        out.success("done")
        out.cleanup()

        # Make sure the signal handler does not call out.cleanup again
        def dummy(self):
            pass
        out.cleanup = type(GaugeOutput.cleanup)(dummy, out, GaugeOutput)

        session = {"dialog": d,
                   "disk": disk,
                   "snapshot": snapshot,
                   "device": dev,
                   "image_os": image_os,
                   "metadata": metadata}

        main_menu(session)
        d.infobox("Thank you for using snf-image-creator. Bye", width=53)
    finally:
        disk.cleanup()

    return 0


def main():

    d = dialog.Dialog(dialog="dialog")

    # Add extra button in dialog library
    dialog._common_args_syntax["extra_button"] = \
        lambda enable: dialog._simple_option("--extra-button", enable)

    dialog._common_args_syntax["extra_label"] = \
        lambda string: ("--extra-label", string)

    while 1:
        try:
            try:
                ret = image_creator(d)
                sys.exit(ret)
            except FatalError as e:
                msg = textwrap.fill(str(e), width=70)
                d.infobox(msg, width=70, title="Fatal Error")
                sys.exit(1)
        except Reset:
            continue

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
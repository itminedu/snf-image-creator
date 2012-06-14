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

from image_creator.output import Output

import sys
from colors import red, green, yellow
from progress.bar import Bar


def output(msg='', new_line=True, decorate=lambda x: x):
    nl = "\n" if new_line else ' '
    sys.stderr.write(decorate(msg) + nl)


def error(msg, new_line=True, colored=True):
    color = red if colored else lambda x: x
    output("Error: %s" % msg, new_line, color)


def warn(msg, new_line=True, colored=True):
    color = yellow if colored else lambda x: x
    output("Warning: %s" % msg, new_line, color)


def success(msg, new_line=True, colored=True):
    color = green if colored else lambda x: x
    output(msg, new_line, color)


class SilentOutput(Output):
    pass


class SimpleOutput(Output):
    def __init__(self, colored=True):
        self.colored = colored

    def error(self, msg, new_line=True):
        error(msg, new_line, self.colored)

    def warn(self, msg, new_line=True):
        warn(msg, new_line, self.colored)

    def success(self, msg, new_line=True):
        success(msg, new_line, self.colored)

    def output(self, msg='', new_line=True):
        output(msg, new_line)


class OutputWthProgress(SimpleOutput):
    class _Progress(Bar):
        MESSAGE_LENGTH = 30

        template = {
            'default': '%(index)d/%(max)d',
            'percent': '%(percent)d%%',
            'b': '%(index)d/%(max)d B',
            'kb': '%(index)d/%(max)d KB',
            'mb': '%(index)d/%(max)d MB'
        }

        def __init__(self, size, title, bar_type='default'):
            super(OutputWthProgress._Progress, self).__init__()
            self.title = title
            self.fill = '#'
            self.bar_prefix = ' ['
            self.bar_suffix = '] '
            self.message = ("%s:" % self.title).ljust(self.MESSAGE_LENGTH)
            self.suffix = self.template[bar_type]
            self.max = size

            # print empty progress bar workaround
            self.goto(1)

        def success(self, result):
            self.output.output("\r%s...\033[K" % self.title, False)
            self.output.success(result)


# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
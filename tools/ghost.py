#!/usr/bin/env python3
"""ghost: compose ghost files with the rules of a spec source tree (rewritten in place).

Usage
    python3 tools/ghost.py SRC_DIR GHOST_FILE [GHOST_FILE ...]

The Makefile runs it on the stage (make GHOST=ghost/x.maude), before the plain copy (DMC=0) or gen-dmc
(DMC=1).  gen-dmc --ghost does the same on a copy of its source dir.  The format of a ghost file and the
composition are described in tools/gen-dmc.py (section "ghost composition").
"""

import importlib.util
import os
import sys


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gen-dmc.py')
    spec = importlib.util.spec_from_file_location('gen_dmc', path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    try:
        report = gen.compose_ghosts(argv[0], argv[1:])
    except gen.GenError as ex:
        sys.stderr.write('ghost: error: %s\n' % ex)
        return 1
    for line in report:
        print('  ghost: %s' % line)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

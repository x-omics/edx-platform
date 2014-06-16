#!/usr/bin/env python

"""
See https://edx-wiki.atlassian.net/wiki/display/ENG/PO+File+workflow

This task merges and compiles the human-readable .po files on the
local filesystem into machine-readable .mo files. This is typically
necessary as part of the build process since these .mo files are
needed by Django when serving the web app.

The configuration file (in edx-platform/conf/locale/config.yaml) specifies which
languages to generate.

"""

import argparse
import logging
import os
import sys

from polib import pofile

from i18n.config import BASE_DIR, CONFIGURATION
from i18n.execute import execute

LOG = logging.getLogger(__name__)
DEVNULL = open(os.devnull, "wb")


def merge(locale, target='django.po', sources=('django-partial.po',), fail_if_missing=True):
    """
    For the given locale, merge the `sources` files to become the `target`
    file.  Note that the target file might also be one of the sources.

    If fail_if_missing is true, and the files to be merged are missing,
    throw an Exception, otherwise return silently.

    If fail_if_missing is false, and the files to be merged are missing,
    just return silently.

    """
    LOG.info('Merging {target} for locale {locale}'.format(target=target, locale=locale))
    locale_directory = CONFIGURATION.get_messages_dir(locale)
    try:
        validate_files(locale_directory, sources)
    except Exception, e:
        if not fail_if_missing:
            return
        raise

    # merged file is merged.po
    merge_cmd = 'msgcat -o merged.po ' + ' '.join(sources)
    execute(merge_cmd, working_directory=locale_directory)

    # clean up redunancies in the metadata
    merged_filename = locale_directory.joinpath('merged.po')
    clean_pofile(merged_filename)

    # rename merged.po -> django.po (default)
    target_filename = locale_directory.joinpath(target)
    os.rename(merged_filename, target_filename)


def merge_files(locale, fail_if_missing=True):
    """
    Merge all the files in `locale`, as specified in config.yaml.
    """
    for target, sources in CONFIGURATION.generate_merge.items():
        merge(locale, target, sources, fail_if_missing)


def clean_pofile(file):
    """
    Clean various aspect of a .po file.

    Fixes:

        - Removes the ,fuzzy flag on metadata.

        - Removes occurrence line numbers so that the generated files don't
          generate a lot of line noise when they're committed.

        - Removes any flags ending with "-format".  Mac gettext seems to add
          these flags, Linux does not, and we don't seem to need them.  By
          removing them, we reduce the unimportant differences that clutter
          diffs as different developers work on the files.

    """
    # Reading in the .po file and saving it again fixes redundancies.
    pomsgs = pofile(file)
    # The msgcat tool marks the metadata as fuzzy, but it's ok as it is.
    pomsgs.metadata_is_fuzzy = False
    for entry in pomsgs:
        # Remove line numbers
        entry.occurrences = [(filename, None) for (filename, lineno) in entry.occurrences]
        # Remove -format flags
        entry.flags = [f for f in entry.flags if not f.endswith("-format")]
    pomsgs.save()


def validate_files(dir, files_to_merge):
    """
    Asserts that the given files exist.
    files_to_merge is a list of file names (no directories).
    dir is the directory (a path object from path.py) in which the files should appear.
    raises an Exception if any of the files are not in dir.
    """
    for path in files_to_merge:
        pathname = dir.joinpath(path)
        if not pathname.exists():
            raise Exception("I18N: Cannot generate because file not found: {0}".format(pathname))


def main(strict=True, verbosity=1):
    """
    Main entry point for script
    """
    rtl_langs = ['he', 'ar', 'fa', 'fa_IR', 'ur']
    ltr_langs = [l for l in CONFIGURATION.translated_locales if l not in rtl_langs]
    for locale in ltr_langs:
        merge_files(locale, fail_if_missing=strict)
    # Dummy text is not required. Don't raise exception if files are missing.
    for locale in CONFIGURATION.dummy_locales:
        merge_files(locale, fail_if_missing=False)

    compile_cmd = 'django-admin.py compilemessages -v{}'.format(verbosity)
    if verbosity:
        stderr = None
    else:
        stderr = DEVNULL
    execute(compile_cmd, working_directory=BASE_DIR, stderr=stderr)


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    # pylint: disable=invalid-name
    parser = argparse.ArgumentParser(description="Generate merged and compiled message files.")
    parser.add_argument("--strict", action='store_true', help="Complain about missing files.")
    parser.add_argument("--verbose", "-v", action="count", default=0)
    args = parser.parse_args()

    main(strict=args.strict, verbosity=args.verbose)

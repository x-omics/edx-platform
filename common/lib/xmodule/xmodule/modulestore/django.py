"""
Module that provides a connection to the ModuleStore specified in the django settings.

Passes settings.MODULESTORE as kwargs to MongoModuleStore
"""

from __future__ import absolute_import

from importlib import import_module
from django.conf import settings
from django.core.cache import get_cache, InvalidCacheBackendError
import django.utils

from xmodule.modulestore.loc_mapper_store import LocMapperStore
import xmodule.modulestore

# We may not always have the request_cache module available
try:
    from request_cache.middleware import RequestCache
    HAS_REQUEST_CACHE = True
except ImportError:
    HAS_REQUEST_CACHE = False


def load_function(path):
    """
    Load a function by name.

    path is a string of the form "path.to.module.function"
    returns the imported python object `function` from `path.to.module`
    """
    module_path, _, name = path.rpartition('.')
    return getattr(import_module(module_path), name)


def create_modulestore_instance(engine, doc_store_config, options, i18n_service=None):
    """
    This will return a new instance of a modulestore given an engine and options
    """
    class_ = load_function(engine)

    _options = {}
    _options.update(options)

    FUNCTION_KEYS = ['render_template']
    for key in FUNCTION_KEYS:
        if key in _options and isinstance(_options[key], basestring):
            _options[key] = load_function(_options[key])

    if HAS_REQUEST_CACHE:
        request_cache = RequestCache.get_request_cache()
    else:
        request_cache = None

    try:
        metadata_inheritance_cache = get_cache('mongo_metadata_inheritance')
    except InvalidCacheBackendError:
        metadata_inheritance_cache = get_cache('default')

    return class_(
        metadata_inheritance_cache_subsystem=metadata_inheritance_cache,
        request_cache=request_cache,
        xblock_mixins=getattr(settings, 'XBLOCK_MIXINS', ()),
        xblock_select=getattr(settings, 'XBLOCK_SELECT_FUNCTION', None),
        doc_store_config=doc_store_config,
        i18n_service=i18n_service or ModuleI18nService(),
        **_options
    )


_MIXED_MODULESTORE = None  # NAATODO - is this thread safe?


def modulestore(name=None):
    """
    Returns the Mixed modulestore
    """

    assert name is None

    global _MIXED_MODULESTORE
    if _MIXED_MODULESTORE is None:
        _MIXED_MODULESTORE = create_modulestore_instance(
            settings.MODULESTORE['default']['ENGINE'],
            settings.MODULESTORE['default'].get('DOC_STORE_CONFIG', {}),
            settings.MODULESTORE['default'].get('OPTIONS', {})
        )

    return _MIXED_MODULESTORE


def clear_existing_modulestores():
    """
    Clear the existing modulestore instances, causing
    them to be re-created when accessed again.

    This is useful for flushing state between unit tests.
    """
    global _MIXED_MODULESTORE, _loc_singleton
    _MIXED_MODULESTORE = None
    # pylint: disable=W0603
    cache = getattr(_loc_singleton, "cache", None)
    if cache:
        cache.clear()
    _loc_singleton = None



_loc_singleton = None
def loc_mapper():
    """
    Get the loc mapper which bidirectionally maps Locations to Locators. Used like modulestore() as
    a singleton accessor.
    """
    # pylint: disable=W0603
    global _loc_singleton
    # pylint: disable=W0212
    if _loc_singleton is None:
        try:
            loc_cache = get_cache('loc_cache')
        except InvalidCacheBackendError:
            loc_cache = get_cache('default')
        # instantiate
        _loc_singleton = LocMapperStore(loc_cache, **settings.DOC_STORE_CONFIG)

    return _loc_singleton


class ModuleI18nService(object):
    """
    Implement the XBlock runtime "i18n" service.

    Mostly a pass-through to Django's translation module.
    django.utils.translation implements the gettext.Translations interface (it
    has ugettext, ungettext, etc), so we can use it directly as the runtime
    i18n service.

    """
    def __getattr__(self, name):
        return getattr(django.utils.translation, name)

    def strftime(self, *args, **kwargs):
        """
        A locale-aware implementation of strftime.
        """
        # This is the wrong place to import this function.  I'm putting it here
        # because the xmodule test suite can't import this module, because
        # Django is not available in that suite.  This function isn't called in
        # that suite, so this hides the import so the test won't fail.
        #
        # As I said, this is wrong.  But Cale says this code will soon be
        # refactored to a place that will be right, and the code can be made
        # right there.  If you are reading this comment after April 1, 2014,
        # then Cale was a liar.
        from util.date_utils import strftime_localized
        return strftime_localized(*args, **kwargs)


# override the definition in the module's init
def get_settings_attr(attr, default=None):
    """
    A standin for getattr(settings..) but doesn't require caller to import settings.
    :param attr:
    :param default:
    """
    return getattr(settings, attr, default)

xmodule.modulestore.get_settings_attr = get_settings_attr

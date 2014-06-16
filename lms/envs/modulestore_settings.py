"""
This file contains helper functions for configuring module_store_setting settings and support for backward compatibility with older formats.
"""


def convert_module_store_setting_if_needed(module_store_setting):
    """
    Converts old-style module_store_setting configuration settings to the new format.
    """

    def convert_old_stores_into_list(old_stores):
        """
        Converts and returns the given stores in old (unordered) dict-style format to the new (ordered) list format
        """
        new_store_list = []
        for store_name, store_settings in old_stores.iteritems():
            store_settings['NAME'] = store_name
            if store_name == 'default':
                new_store_list.insert(0, store_settings)
            else:
                new_store_list.append(store_settings)
        return new_store_list

    if module_store_setting is None:
        return None

    if module_store_setting['default']['ENGINE'] != 'xmodule.modulestore.mixed.MixedModuleStore':
        # convert to using mixed module_store
        new_module_store_setting = {
            "default": {
                "ENGINE": "xmodule.modulestore.mixed.MixedModuleStore",
                "OPTIONS": {
                    "mappings": {},
                    "reference_type": "Location",
                    "stores": []
                }
            }
        }

        # copy the old configurations into the new settings
        new_module_store_setting['default']['OPTIONS']['stores'] = convert_old_stores_into_list(
            module_store_setting
        )
        module_store_setting = new_module_store_setting

    elif isinstance(module_store_setting['default']['OPTIONS']['stores'], dict):
        # convert old-style (unordered) dict to (an ordered) list
        module_store_setting['default']['OPTIONS']['stores'] = convert_old_stores_into_list(
            module_store_setting['default']['OPTIONS']['stores']
        )

        assert isinstance(module_store_setting['default']['OPTIONS']['stores'], list)
    return module_store_setting

def update_module_store_settings(
        module_store_setting,
        doc_store_settings=None,
        module_store_options=None,
        xml_store_options=None,
):
    """
    Updates the settings for each store defined in the given module_store_setting settings
    with the given doc store configuration and options, overwriting existing keys.
    """
    for store in module_store_setting['default']['OPTIONS']['stores']:
        if store['NAME'] == 'xml':
            xml_store_options and store['OPTIONS'].update(xml_store_options)
        else:
            module_store_options and store['OPTIONS'].update(module_store_options)
            doc_store_settings and store['DOC_STORE_CONFIG'].update(doc_store_settings)

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright (C) 2014 Midokura SARL.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import webob

from neutron.api import extensions
from neutron.api.v2 import base
from neutron.api.v2 import resource
from neutron import manager
from neutron.openstack.common import log as logging
from neutron import wsgi

LOG = logging.getLogger(__name__)
PLURAL_NAME_MAP = {}


def midonet_extension(cls):
    """Wrapper automatically generates extension methods based on the name of
    the given class.

    This defines get_name, get_alias, get_description, get_namespace,
    get_updated, get_resources if they're not defined in the decorated class.
    If some of them are already defined, this decorator doesn't override them
    and just use them as they're.

    :param cls: A class to be set required extension methods.
    :return: The class where the extension methods are set.
    """
    setattr(cls, 'EXT_ALIAS', cls.__name__.lower())
    collection_name = PLURAL_NAME_MAP.get(cls.EXT_ALIAS) or (
        '%ss' % cls.EXT_ALIAS)
    setattr(cls, 'COLLECTION_NAME', collection_name)
    setattr(cls, 'RESOURCE_ATTRIBUTE_MAP',
            {collection_name: cls.RESOURCE_ATTRIBUTE_MAP})
    setattr(cls, 'CHILD_RESOURCE_ATTRIBUTE_MAP',
            getattr(cls, 'CHILD_RESOURCE_ATTRIBUTE_MAP', []))

    def get_name(cls):
        return cls.__name__.capitalize()

    def get_alias(cls):
        return cls.EXT_ALIAS

    def get_description(cls):
        return cls.__doc__

    def get_namespace(cls):
        return "http://docs.openstack.org/ext/neutron/%s/api/v1.0" % (
            cls.__name__.lower())

    def get_updated(cls):
        return "2014-09-01T00:00:00+09:00"

    def _get_resource_controller(alias):
        class ResourceController(wsgi.Controller):
            def get_plugin(self):
                plugin = manager.NeutronManager.get_service_plugins().get(
                    alias)
                if not plugin:
                    msg = _("No plugin for Host extension")
                    LOG.error(msg)
                    raise webob.exc.HTTPNotFound(msg)
                return plugin

            def index(self, request, **kwargs):
                plugin = self.get_plugin()
                collection_name = PLURAL_NAME_MAP.get(alias, '%ss' % alias)
                return getattr(plugin, 'get_%s' % collection_name)(
                    request.context, **kwargs)
        return ResourceController

    # The default implementation may not go well with some resouces. In that
    # case please define your own get_resources.
    def get_resources(cls):
        exts = list()
        plugin = manager.NeutronManager.get_plugin()
        resource_name = cls.EXT_ALIAS
        collection_name = cls.COLLECTION_NAME
        params = cls.RESOURCE_ATTIRBUTE_MAP.get(collection_name, dict())
        controller = base.create_resource(collection_name, resource_name,
                                          plugin, params, allow_bulk=False)
        ex = extensions.ResourceExtension(collection_name, controller)
        exts.append(ex)

        for alias, params in cls.CHILD_RESOURCE_ATTRIBUTE_MAP.items():
            parent = dict(member_name=cls.EXT_ALIAS,
                          collection_name=cls.COLLECTION_NAME)
            child_resource = resource.Resource(
                _get_resource_controller(alias)(), base.FAULT_MAP)
            child_extension = extensions.ResourceExtension(
                alias, child_resource,
                parent=parent, attr_map=params)
            exts.append(child_extension)
        return exts

    required_methods = [get_name, get_alias, get_description,
                        get_namespace, get_updated, get_resources]
    for method in required_methods:
        try:
            m = getattr(cls, method.__name__)
            for b in cls.__bases__:
                if m == getattr(extensions.ExtensionDescriptor,
                                method.__name__):
                    setattr(cls, method.__name__, classmethod(method))
        except AttributeError:
            setattr(cls, method.__name__, classmethod(method))

    return cls



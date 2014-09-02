# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (C) 2012 Midokura Japan K.K.
# Copyright (C) 2013 Midokura PTE LTD
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Ryu Ishimoto, Midokura Japan KK

import abc

import six

from neutron.api import extensions
from neutron.api.v2 import base
from neutron import manager

HOST = 'host'
HOSTS = '%ss' % HOST
HOST_INTERFACE = 'host_interface'
HOST_INTERFACES = '%ss' % HOST_INTERFACE
HOST_VERSION = 'host_version'
HOST_VERSIONS = '%ss' % HOST_VERSION


RESOURCE_ATTRIBUTE_MAP = {
    HOSTS: {
        'id': {'allow_post': False, 'allow_put': False,
               'validate': {'type:uuid': None},
               'is_visible': True,
               'primary_key': True},
        'name': {'allow_post': True, 'allow_put': True,
                 'validate': {'type:string': None},
                 'is_visible': True, 'default': ''},
        'addresses': {'allow_post': True, 'allow_put': True,
                      'validate': {'type:string': None},
                      'is_visible': True, 'default': ''},
        'alive': {'allow_post': False, 'allow_put': False,
                  'validate': {'type:string': None},
                  'is_visible': True, 'default': False},
        'flooding_proxy_weight': {'allow_post': False, 'allow_put': False,
                                  'validate': {'type:string': None},
                                  'is_visible': True, 'default': False},
    },
    HOST_INTERFACES: {
        'host_id': {'allow_post': False, 'allow_put': False,
                    'validate': {'type:uuid': None},
                    'is_visible': True,
                    'primary_key': True},
        'name': {'allow_post': True, 'allow_put': True,
                 'validate': {'type:string': None},
                 'is_visible': True, 'default': ''},
        'mac': {'allow_post': True, 'allow_put': True,
                'validate': {'type:string': None},
                'is_visible': True, 'default': ''},
        'mtu': {'allow_post': True, 'allow_put': True,
                'validate': {'type:string': None},
                'is_visible': True, 'default': ''},
        'status': {'allow_post': True, 'allow_put': True,
                   'validate': {'type:string': None},
                   'is_visible': True, 'default': ''},
        'type': {'allow_post': True, 'allow_put': True,
                 'validate': {'type:string': None},
                 'is_visible': True, 'default': ''},
        'endpoint': {'allow_post': True, 'allow_put': True,
                     'validate': {'type:string': None},
                     'is_visible': True, 'default': ''},
        'port_type': {'allow_post': True, 'allow_put': True,
                      'validate': {'type:string': None},
                      'is_visible': True, 'default': ''},
        'addresses': {'allow_post': True, 'allow_put': True,
                      'validate': {'type:string': None},
                      'is_visible': True, 'default': ''},
    },
    HOST_VERSIONS: {
        'host_id': {'allow_post': False, 'allow_put': False,
                    'validate': {'type:uuid': None},
                    'is_visible': True,
                    'primary_key': True},
        'version': {'allow_post': True, 'allow_put': True,
                    'validate': {'type:string': None},
                    'is_visible': True, 'default': ''},
    },
}


class Host(extensions.ExtensionDescriptor):
    """Host extension."""

    @classmethod
    def get_name(cls):
        return "Midonet Host Extension"

    @classmethod
    def get_alias(cls):
        return "host"

    @classmethod
    def get_description(cls):
        return ("Host abstraction for basic host-related features")

    @classmethod
    def get_namespace(cls):
        return "http://docs.openstack.org/ext/host/api/v1.0"

    @classmethod
    def get_updated(cls):
        return "2014-07-20T10:00:00-00:00"

    @classmethod
    def get_resources(cls):
        """Returns Ext Resources."""
        exts = []
        plugin = manager.NeutronManager.get_plugin()

        # hosts
        resource_name = HOST
        collection_name = HOSTS
        params = RESOURCE_ATTRIBUTE_MAP.get(collection_name, dict())
        controller = base.create_resource(collection_name,
                                          resource_name,
                                          plugin,
                                          params,
                                          allow_bulk=False)

        ex = extensions.ResourceExtension(collection_name, controller)
        exts.append(ex)

        # host interfaces
        resource_name = HOST_INTERFACE
        collection_name = HOST_INTERFACES
        params = RESOURCE_ATTRIBUTE_MAP.get(collection_name, dict())
        controller = base.create_resource(collection_name,
                                          resource_name,
                                          plugin,
                                          params,
                                          allow_bulk=False)

        ex = extensions.ResourceExtension(collection_name, controller)
        exts.append(ex)

        # host interfaces
        resource_name = HOST_VERSION
        collection_name = HOST_VERSIONS
        params = RESOURCE_ATTRIBUTE_MAP.get(collection_name, dict())
        controller = base.create_resource(collection_name,
                                          resource_name,
                                          plugin,
                                          params,
                                          allow_bulk=False)

        ex = extensions.ResourceExtension(collection_name, controller)
        exts.append(ex)
        return exts

    def update_attributes_map(self, attributes):
        for resource, attrs in RESOURCE_ATTRIBUTE_MAP.iteritems():
            extended_attrs = attributes.get(resource)
            if extended_attrs:
                attrs.update(extended_attrs)

        super(Host, self).update_attributes_map(
            attributes, extension_attrs_map=RESOURCE_ATTRIBUTE_MAP)

    def get_extended_resources(self, version):
        if version == "2.0":
            return RESOURCE_ATTRIBUTE_MAP
        else:
            return {}


@six.add_metaclass(abc.ABCMeta)
class HostPluginBase(object):

    @abc.abstractmethod
    def create_host(self, context, host):
        pass

    @abc.abstractmethod
    def update_host(self, context, id, host):
        pass

    @abc.abstractmethod
    def get_host(self, context, host):
        pass

    @abc.abstractmethod
    def delete_host(self, context, id):
        pass

    @abc.abstractmethod
    def get_hosts(self, context):
        pass

    @abc.abstractmethod
    def create_host_interface(self, context, host_interface):
        pass

    @abc.abstractmethod
    def update_host_interface(self, context, id, host_interface):
        pass

    @abc.abstractmethod
    def get_host_interface(self, context, host_interface):
        pass

    @abc.abstractmethod
    def delete_host_interface(self, context, id):
        pass

    @abc.abstractmethod
    def get_host_interfaces(self, context):
        pass

    @abc.abstractmethod
    def create_host_version(self, context, host_version):
        pass

    @abc.abstractmethod
    def update_host_version(self, context, id, host_version):
        pass

    @abc.abstractmethod
    def get_host_version(self, context, host_version):
        pass

    @abc.abstractmethod
    def delete_host_version(self, context, id):
        pass

    @abc.abstractmethod
    def get_host_versions(self, context):
        pass

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
from midonet.neutron.common import util

from neutron.api import extensions
from neutron.openstack.common import log as logging


LOG = logging.getLogger(__name__)


@util.midonet_extension
class Tunnelzone(extensions.ExtensionDescriptor):
    """Tunnel zone represents a group in which hosts can be included to form an
    isolated zone for tunneling.
    """

    RESOURCE_ATTRIBUTE_MAP = {
        'id': {'allow_post': False, 'allow_put': False,
               'validate': {'type:string': None}, 'is_visible': True},
        'name': {'allow_post': True, 'allow_put': True, 'default': '',
                 'validate': {'type:string': None}, 'is_visible': True},
        'type': {'allow_post': True, 'allow_put': True, 'default': 'GRE',
                 'validate': {'type:values': ['GRE']}, 'is_visible': True}
    }

    CHILD_RESOURCE_ATTRIBUTE_MAP = {
        'tunnelzonehost': {
            'id': {'allow_post': False, 'allow_put': False,
                   'validate': {'type:uuid': None}, 'is_visible': True},
            'tunnelZoneId': {'allow_post': True, 'allow_put': True,
                             'validate': {'type:uuid': None},
                             'is_visible': True},
            'tunnelZone': {'allow_post': False, 'allow_put': False,
                           'validate': {'type:url': None}, 'is_visible': True},
            'hostId': {'allow_post': True, 'allow_put': True,
                       'validate': {'type:uuid': None}, 'is_visible': True},
            'host': {'allow_post': False, 'allow_put': False,
                     'validate': {'type:uuid': None}, 'is_visible': True},
            'ipAddress': {'allow_post': False, 'allow_put': False,
                          'validate': {'type:uuid': None}, 'is_visible': True}
        }
    }

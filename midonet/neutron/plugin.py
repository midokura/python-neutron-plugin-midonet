# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (C) 2012 Midokura Japan K.K.
# Copyright (C) 2013 Midokura PTE LTD
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
#
# @author: Takaaki Suzuki, Midokura Japan KK
# @author: Tomoe Sugihara, Midokura Japan KK
# @author: Ryu Ishimoto, Midokura Japan KK
# @author: Rossella Sblendido, Midokura Japan KK
# @author: Duarte Nunes, Midokura Japan KK

from webob import exc as w_exc

from midonetclient import exc
from midonetclient.neutron import client as n_client

from oslo.config import cfg

from sqlalchemy import exc as sa_exc

from neutron.api.rpc.handlers import dhcp_rpc
from neutron.api.v2 import base
from neutron.common import exceptions as n_exc
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron.common import utils
from neutron.db import agents_db
from neutron.db import agentschedulers_db
from neutron.db import api as db
from neutron.db import db_base_plugin_v2
from neutron.db import external_net_db
from neutron.db import l3_gwmode_db
from neutron.db.loadbalancer import loadbalancer_db
from neutron.db import portbindings_db
from neutron.db import routedserviceinsertion_db as rsi_db
from neutron.db import securitygroups_db
from neutron.extensions import portbindings
from neutron.extensions import routedserviceinsertion as rsi
from neutron.extensions import securitygroup as ext_sg
from neutron.openstack.common import excutils
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants
from neutron.plugins.midonet.common import config  # noqa

LOG = logging.getLogger(__name__)


def handle_api_error(fn):
    """Wrapper for methods that throws custom exceptions."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (w_exc.HTTPException, exc.MidoApiConnectionError) as ex:
            raise MidonetApiException(msg=ex)
    return wrapped


class MidonetApiException(n_exc.NeutronException):
        message = _("MidoNet API error: %(msg)s")


class MidonetPluginException(n_exc.NeutronException):
    message = _("%(msg)s")


def generate_methods(methods):
    """Decorator for classes that represents which HTTP methods are required by
    the classes.
    """
    ALLOWED_METHODS = (base.Controller.LIST,
                       base.Controller.SHOW,
                       base.Controller.CREATE,
                       base.Controller.UPDATE,
                       base.Controller.DELETE)

    @handle_api_error
    def create_resource(self, context, id, resource):
        pass

    @handle_api_error
    def update_resource(self, context, id, resource):
        pass

    @handle_api_error
    def get_resource(self, context, id, fields):
        pass

    @handle_api_error
    def get_resources(self, context, filters, fields):
        pass

    @handle_api_error
    def delete_resource(self, context, id):
        pass

    AVAILABLE_METHODS = {base.Controller.LIST: get_resources,
                         base.Controller.SHOW: get_resource,
                         base.Controller.CREATE: create_resource,
                         base.Controller.UPDATE: update_resource,
                         base.Controller.DELETE: delete_resource}

    required_methods = [method for method in methods
                        if method in ALLOWED_METHODS]
    def wrapper(cls):
        # Use the first capitalzed word as an alias.
        [capitalized_resource] = re.findall('^[A-Z][a-z0-9]*', cls.__name__)
        alias = capitalized_resource.lower()
        setattr(cls, 'ALIAS', alias)
        for method in required_methods:
            method_name = method + '_' + alias
            try:
                getattr(cls, method_name)
            except AttributeError as e:
                setattr(cls, method_name, AVAILABLE_METHODS[method])
        return cls

    return wrapper


class MidonetPluginV2(db_base_plugin_v2.NeutronDbPluginV2,
                      portbindings_db.PortBindingMixin,
                      external_net_db.External_net_db_mixin,
                      l3_gwmode_db.L3_NAT_db_mixin,
                      agentschedulers_db.DhcpAgentSchedulerDbMixin,
                      securitygroups_db.SecurityGroupDbMixin,
                      rsi_db.RoutedServiceInsertionDbMixin,
                      loadbalancer_db.LoadBalancerPluginDb):

    supported_extension_aliases = ['agent',
                                   'binding',
                                   'dhcp_agent_scheduler',
                                   'external-net',
                                   'router',
                                   'quotas',
                                   'security-group',
                                   'routed-service-insertion',
                                   'lbaas']
    __native_bulk_support = True

    def __init__(self):
        super(MidonetPluginV2, self).__init__()

        # Instantiate MidoNet API client
        conf = cfg.CONF.MIDONET
        self.api_cli = n_client.MidonetClient(conf.midonet_uri, conf.username,
                                              conf.password,
                                              project_id=conf.project_id)

        self.setup_rpc()
        self.repair_quotas_table()

        self.base_binding_dict = {
            portbindings.VIF_TYPE: portbindings.VIF_TYPE_MIDONET,
            portbindings.VIF_DETAILS: {
                # TODO(rkukura): Replace with new VIF security details
                portbindings.CAP_PORT_FILTER:
                'security-group' in self.supported_extension_aliases}}
        self.network_scheduler = importutils.import_object(
            cfg.CONF.network_scheduler_driver
        )

    def setup_rpc(self):
        # RPC support
        self.topic = topics.PLUGIN
        self.conn = n_rpc.create_connection(new=True)
        self.endpoints = [dhcp_rpc.DhcpRpcCallback(),
                          agents_db.AgentExtRpcCallback()]
        self.conn.create_consumer(self.topic, self.endpoints,
                                  fanout=False)
        # Consume from all consumers in a thread
        self.conn.consume_in_threads()

    def repair_quotas_table(self):
        query = ("CREATE TABLE `quotas` ( `id` varchar(36) NOT NULL, "
                 "`tenant_id` varchar(255) DEFAULT NULL, "
                 "`resource` varchar(255) DEFAULT NULL, "
                 "`limit` int(11) DEFAULT NULL, "
                 "PRIMARY KEY (`id`) ) ENGINE=InnoDB DEFAULT CHARSET=utf8;")
        session = db.get_session()
        try:
            session.execute(query)
        except sa_exc.OperationalError:
            # If the table already exists, then this is expected.
            pass

    def _process_create_network(self, context, network):

        net_data = network['network']
        tenant_id = self._get_tenant_id_for_create(context, net_data)
        net_data['tenant_id'] = tenant_id
        self._ensure_default_security_group(context, tenant_id)

        with context.session.begin(subtransactions=True):
            net = super(MidonetPluginV2, self).create_network(context, network)
            self._process_l3_create(context, net, net_data)

        return net

    @handle_api_error
    def create_network(self, context, network):
        """Create Neutron network.

        Create a new Neutron network and its corresponding MidoNet bridge.
        """
        LOG.info(_('MidonetPluginV2.create_network called: network=%r'),
                 network)

        net = self._process_create_network(context, network)

        try:
            self.api_cli.create_network(net)
        except Exception as ex:
            LOG.error(_("Failed to create a network %(net_id)s in Midonet:"
                        "%(err)s"), {"net_id": net["id"], "err": ex})
            with excutils.save_and_reraise_exception():
                super(MidonetPluginV2, self).delete_network(context, net['id'])

        LOG.info(_("MidonetPluginV2.create_network exiting: net=%r"), net)
        return net

    @handle_api_error
    def update_network(self, context, id, network):
        """Update Neutron network.

        Update an existing Neutron network and its corresponding MidoNet
        bridge.
        """
        LOG.info(_("MidonetPluginV2.update_network called: id=%(id)r, "
                   "network=%(network)r"), {'id': id, 'network': network})

        with context.session.begin(subtransactions=True):
            net = super(MidonetPluginV2, self).update_network(
                context, id, network)

            self._process_l3_update(context, net, network['network'])
            self.api_cli.update_network(id, net)

        LOG.info(_("MidonetPluginV2.update_network exiting: net=%r"), net)
        return net

    @handle_api_error
    @utils.synchronized('midonet-critical-section', external=True)
    def delete_network(self, context, id):
        """Delete a network and its corresponding MidoNet bridge."""
        LOG.info(_("MidonetPluginV2.delete_network called: id=%r"), id)

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_network(context, id)
            self.api_cli.delete_network(id)

        LOG.info(_("MidonetPluginV2.delete_network exiting: id=%r"), id)

    @handle_api_error
    def create_subnet(self, context, subnet):
        """Create Neutron subnet.

        Creates a Neutron subnet and a DHCP entry in MidoNet bridge.
        """
        LOG.info(_("MidonetPluginV2.create_subnet called: subnet=%r"), subnet)

        sn_entry = super(MidonetPluginV2, self).create_subnet(context, subnet)

        try:
            self.api_cli.create_subnet(sn_entry)
        except Exception as ex:
            LOG.error(_("Failed to create a subnet %(s_id)s in Midonet:"
                        "%(err)s"), {"s_id": sn_entry["id"], "err": ex})
            with excutils.save_and_reraise_exception():
                super(MidonetPluginV2, self).delete_subnet(context,
                                                           sn_entry['id'])

        LOG.info(_("MidonetPluginV2.create_subnet exiting: sn_entry=%r"),
                 sn_entry)
        return sn_entry

    @handle_api_error
    def delete_subnet(self, context, id):
        """Delete Neutron subnet.

        Delete neutron network and its corresponding MidoNet bridge.
        """
        LOG.info(_("MidonetPluginV2.delete_subnet called: id=%s"), id)

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_subnet(context, id)
            self.api_cli.delete_subnet(id)

        LOG.info(_("MidonetPluginV2.delete_subnet exiting"))

    @handle_api_error
    def update_subnet(self, context, id, subnet):
        """Update the subnet with new info.
        """
        LOG.info(_("MidonetPluginV2.update_subnet called: id=%s"), id)

        with context.session.begin(subtransactions=True):
            s = super(MidonetPluginV2, self).update_subnet(context, id, subnet)
            self.api_cli.update_subnet(id, s)

        return s

    def _process_create_port(self, context, port):
        """Create a L2 port in Neutron/MidoNet."""
        port_data = port['port']
        with context.session.begin(subtransactions=True):
            # Create a Neutron port
            new_port = super(MidonetPluginV2, self).create_port(context, port)

            # Make sure that the port created is valid
            if "id" not in new_port:
                raise n_exc.BadRequest(resource='port',
                                       msg="Invalid port created")

            # Update fields
            port_data.update(new_port)

            # Bind security groups to the port
            self._ensure_default_security_group_on_port(context, port)
            sg_ids = self._get_security_groups_on_port(context, port)
            self._process_port_create_security_group(context, new_port, sg_ids)

            self._process_portbindings_create_and_update(context, port_data,
                                                         new_port)

        return new_port

    @handle_api_error
    @utils.synchronized('midonet-critical-section', external=True)
    def create_port(self, context, port):
        """Create a L2 port in Neutron/MidoNet."""
        LOG.info(_("MidonetPluginV2.create_port called: port=%r"), port)

        new_port = self._process_create_port(context, port)

        try:
            self.api_cli.create_port(new_port)
        except Exception as ex:
            LOG.error(_("Failed to create a port %(new_port)s: %(err)s"),
                      {"new_port": new_port, "err": ex})
            with excutils.save_and_reraise_exception():
                super(MidonetPluginV2, self).delete_port(context,
                                                         new_port['id'])

        LOG.info(_("MidonetPluginV2.create_port exiting: port=%r"), new_port)
        return new_port

    @handle_api_error
    @utils.synchronized('midonet-critical-section', external=True)
    def delete_port(self, context, id, l3_port_check=True):
        """Delete a neutron port and corresponding MidoNet bridge port."""
        LOG.info(_("MidonetPluginV2.delete_port called: id=%(id)s "
                   "l3_port_check=%(l3_port_check)r"),
                 {'id': id, 'l3_port_check': l3_port_check})

        # if needed, check to see if this is a port owned by
        # and l3-router.  If so, we should prevent deletion.
        if l3_port_check:
            self.prevent_l3_port_deletion(context, id)

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).disassociate_floatingips(
                context, id, do_notify=False)
            super(MidonetPluginV2, self).delete_port(context, id)
            self.api_cli.delete_port(id)

        LOG.info(_("MidonetPluginV2.delete_port exiting: id=%r"), id)

    def _process_port_update(self, context, id, in_port, out_port):

        has_sg = self._check_update_has_security_groups(in_port)
        delete_sg = self._check_update_deletes_security_groups(in_port)

        if delete_sg or has_sg:
            # delete the port binding and read it with the new rules.
            self._delete_port_security_group_bindings(context, id)
            sg_ids = self._get_security_groups_on_port(context, in_port)
            self._process_port_create_security_group(context, out_port, sg_ids)

    @handle_api_error
    def update_port(self, context, id, port):
        """Handle port update, including security groups and fixed IPs."""
        LOG.info(_("MidonetPluginV2.update_port called: id=%(id)s "
                   "port=%(port)r"), {'id': id, 'port': port})
        with context.session.begin(subtransactions=True):

            # update the port DB
            p = super(MidonetPluginV2, self).update_port(context, id, port)

            self._process_port_update(context, id, port, p)
            self._process_portbindings_create_and_update(context,
                                                         port['port'], p)
            self.api_cli.update_port(id, p)

        LOG.info(_("MidonetPluginV2.update_port exiting: p=%r"), p)
        return p

    @handle_api_error
    def create_router(self, context, router):
        """Handle router creation.

        When a new Neutron router is created, its corresponding MidoNet router
        is also created.  In MidoNet, this router is initialized with chains
        for inbound and outbound traffic, which will be used to hold other
        chains that include various rules, such as NAT.

        :param router: Router information provided to create a new router.
        """
        LOG.info(_("MidonetPluginV2.create_router called: router=%(router)s"),
                 {"router": router})
        r = super(MidonetPluginV2, self).create_router(context, router)
        try:
            self.api_cli.create_router(r)
        except Exception as ex:
            LOG.error(_("Failed to create a router %(r_id)s in Midonet:"
                        "%(err)s"), {"r_id": r["id"], "err": ex})
            with excutils.save_and_reraise_exception():
                super(MidonetPluginV2, self).delete_router(context, r['id'])

        LOG.info(_("MidonetPluginV2.create_router exiting: "
                   "router=%(router)s."), {"router": r})
        return r

    @handle_api_error
    def update_router(self, context, id, router):
        """Handle router updates."""
        LOG.info(_("MidonetPluginV2.update_router called: id=%(id)s "
                   "router=%(router)r"), {"id": id, "router": router})

        with context.session.begin(subtransactions=True):
            r = super(MidonetPluginV2, self).update_router(context, id, router)
            self.api_cli.update_router(id, r)

        LOG.info(_("MidonetPluginV2.update_router exiting: router=%r"), r)
        return r

    @handle_api_error
    def delete_router(self, context, id):
        """Handler for router deletion.

        Deleting a router on Neutron simply means deleting its corresponding
        router in MidoNet.

        :param id: router ID to remove
        """
        LOG.info(_("MidonetPluginV2.delete_router called: id=%s"), id)

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_router(context, id)
            self.api_cli.delete_router(id)

        LOG.info(_("MidonetPluginV2.delete_router exiting: id=%s"), id)

    @handle_api_error
    def add_router_interface(self, context, router_id, interface_info):
        """Handle router linking with network."""
        LOG.info(_("MidonetPluginV2.add_router_interface called: "
                   "router_id=%(router_id)s "
                   "interface_info=%(interface_info)r"),
                 {'router_id': router_id, 'interface_info': interface_info})

        info = super(MidonetPluginV2, self).add_router_interface(
            context, router_id, interface_info)

        try:
            self.api_cli.add_router_interface(router_id, info)
        except Exception:
            LOG.error(_("Failed to create MidoNet resources to add router "
                        "interface. info=%(info)s, router_id=%(router_id)s"),
                      {"info": info, "router_id": router_id})
            with excutils.save_and_reraise_exception():
                self.remove_router_interface(context, router_id, info)

        LOG.info(_("MidonetPluginV2.add_router_interface exiting: info=%r"),
                 info)
        return info

    @handle_api_error
    def remove_router_interface(self, context, router_id, interface_info):
        """Handle router un-linking with network."""
        LOG.info(_("MidonetPluginV2.remove_router_interface called: "
                   "router_id=%(router_id)s "
                   "interface_info=%(interface_info)r"),
                 {'router_id': router_id, 'interface_info': interface_info})

        with context.session.begin(subtransactions=True):
            info = super(MidonetPluginV2, self).remove_router_interface(
                context, router_id, interface_info)
            self.api_cli.remove_router_interface(router_id, interface_info)

        LOG.info(_("MidonetPluginV2.remove_router_interface exiting: "
                   "info=%r"), info)
        return info

    @handle_api_error
    def create_floatingip(self, context, floatingip):
        """Handle floating IP creation."""
        LOG.info(_("MidonetPluginV2.create_floatingip called: ip=%r"),
                 floatingip)

        fip = super(MidonetPluginV2, self).create_floatingip(context,
                                                             floatingip)
        try:
            self.api_cli.create_floating_ip(fip)
        except Exception as ex:
            LOG.error(_("Failed to create floating ip %(fip)s: %(err)s"),
                      {"fip": fip, "err": ex})
            with excutils.save_and_reraise_exception():
                # Try removing the fip
                self.delete_floatingip(context, fip['id'])

        LOG.info(_("MidonetPluginV2.create_floatingip exiting: fip=%r"),
                 fip)
        return fip

    @handle_api_error
    def delete_floatingip(self, context, id):
        """Handle floating IP deletion."""
        LOG.info(_("MidonetPluginV2.delete_floatingip called: id=%s"), id)

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_floatingip(context, id)
            self.api_cli.delete_floating_ip(id)

        LOG.info(_("MidonetPluginV2.delete_floatingip exiting: id=%r"), id)

    @handle_api_error
    def update_floatingip(self, context, id, floatingip):
        """Handle floating IP association and disassociation."""
        LOG.info(_("MidonetPluginV2.update_floatingip called: id=%(id)s "
                   "floatingip=%(floatingip)s "),
                 {'id': id, 'floatingip': floatingip})

        with context.session.begin(subtransactions=True):
            fip = super(MidonetPluginV2, self).update_floatingip(context, id,
                                                                 floatingip)
            self.api_cli.update_floating_ip(id, fip)

        LOG.info(_("MidonetPluginV2.update_floating_ip exiting: fip=%s"), fip)
        return fip

    @handle_api_error
    def create_security_group(self, context, security_group, default_sg=False):
        """Create security group.

        Create a new security group, including the default security group.
        In MidoNet, this means creating a pair of chains, inbound and outbound,
        as well as a new port group.
        """
        LOG.info(_("MidonetPluginV2.create_security_group called: "
                   "security_group=%(security_group)s "
                   "default_sg=%(default_sg)s "),
                 {'security_group': security_group, 'default_sg': default_sg})

        sg = security_group.get('security_group')
        tenant_id = self._get_tenant_id_for_create(context, sg)
        if not default_sg:
            self._ensure_default_security_group(context, tenant_id)

        # Create the Neutron sg first
        sg = super(MidonetPluginV2, self).create_security_group(
            context, security_group, default_sg)

        try:
            # Process the MidoNet side
            self.api_cli.create_security_group(sg)
        except Exception:
            LOG.error(_("Failed to create MidoNet resources for sg %(sg)r"),
                      {"sg": sg})
            with excutils.save_and_reraise_exception():
                super(MidonetPluginV2, self).delete_security_group(context,
                                                                   sg['id'])

        LOG.info(_("MidonetPluginV2.create_security_group exiting: sg=%r"), sg)
        return sg

    @handle_api_error
    def delete_security_group(self, context, id):
        """Delete chains for Neutron security group."""
        LOG.info(_("MidonetPluginV2.delete_security_group called: id=%s"), id)

        sg = super(MidonetPluginV2, self).get_security_group(context, id)
        if not sg:
            raise ext_sg.SecurityGroupNotFound(id=id)

        if sg["name"] == 'default' and not context.is_admin:
            raise ext_sg.SecurityGroupCannotRemoveDefault()

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_security_group(context, id)
            self.api_cli.delete_security_group(id)

        LOG.info(_("MidonetPluginV2.delete_security_group exiting: id=%r"), id)

    @handle_api_error
    def create_security_group_rule(self, context, security_group_rule):
        """Create a security group rule

        Create a security group rule in the Neutron DB and corresponding
        MidoNet resources in its data store.
        """
        LOG.info(_("MidonetPluginV2.create_security_group_rule called: "
                   "security_group_rule=%(security_group_rule)r"),
                 {'security_group_rule': security_group_rule})

        rule = super(MidonetPluginV2, self).create_security_group_rule(
            context, security_group_rule)

        try:
            self.api_cli.create_security_group_rule(rule)
        except Exception as ex:
            LOG.error(_('Failed to create security group rule %(sg)s,'
                      'error: %(err)s'), {'sg': rule, 'err': ex})
            with excutils.save_and_reraise_exception():
                super(MidonetPluginV2, self).delete_security_group_rule(
                    context, rule['id'])

        LOG.info(_("MidonetPluginV2.create_security_group_rule exiting: "
                   "rule=%r"), rule)
        return rule

    @handle_api_error
    def create_security_group_rule_bulk(self, context, security_group_rules):
        """Create multiple security group rules

        Create multiple security group rules in the Neutron DB and
        corresponding MidoNet resources in its data store.
        """
        LOG.info(_("MidonetPluginV2.create_security_group_rule_bulk called: "
                   "security_group_rules=%(security_group_rules)r"),
                 {'security_group_rules': security_group_rules})

        rules = super(
            MidonetPluginV2, self).create_security_group_rule_bulk_native(
                context, security_group_rules)
        try:
            self.api_cli.create_security_group_rule_bulk(rules)
        except Exception as ex:
            LOG.error(_("Failed to create bulk security group rules %(sg)s, "
                        "error: %(err)s"), {"sg": rules, "err": ex})
            with excutils.save_and_reraise_exception():
                for rule in rules:
                    super(MidonetPluginV2, self).delete_security_group_rule(
                        context, rule['id'])

        LOG.info(_("MidonetPluginV2.create_security_group_rule_bulk exiting: "
                   "rules=%r"), rules)
        return rules

    @handle_api_error
    def delete_security_group_rule(self, context, sg_rule_id):
        """Delete a security group rule

        Delete a security group rule from the Neutron DB and corresponding
        MidoNet resources from its data store.
        """
        LOG.info(_("MidonetPluginV2.delete_security_group_rule called: "
                   "sg_rule_id=%s"), sg_rule_id)

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_security_group_rule(context,
                                                                    sg_rule_id)
            self.api_cli.delete_security_group_rule(sg_rule_id)

        LOG.info(_("MidonetPluginV2.delete_security_group_rule exiting: "
                   "id=%r"), id)

    @handle_api_error
    def create_vip(self, context, vip):
        LOG.debug("MidonetPluginV2.create_vip called: %(vip)r",
                  {'vip': vip})

        with context.session.begin(subtransactions=True):
            v = super(MidonetPluginV2, self).create_vip(context, vip)
            self.api_cli.create_vip(v)
            v['status'] = constants.ACTIVE
            self.update_status(context, loadbalancer_db.Vip, v['id'],
                               v['status'])

        LOG.debug("MidonetPluginV2.create_vip exiting: id=%r", v['id'])
        return v

    @handle_api_error
    def delete_vip(self, context, id):
        LOG.debug("MidonetPluginV2.delete_vip called: id=%(id)r",
                  {'id': id})

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_vip(context, id)
            self.api_cli.delete_vip(id)

        LOG.debug("MidonetPluginV2.delete_vip existing: id=%(id)r",
                  {'id': id})

    @handle_api_error
    def update_vip(self, context, id, vip):
        LOG.debug("MidonetPluginV2.update_vip called: id=%(id)r, "
                  "vip=%(vip)r", {'id': id, 'vip': vip})

        with context.session.begin(subtransactions=True):
            v = super(MidonetPluginV2, self).update_vip(context, id, vip)
            self.api_cli.update_vip(id, v)

        LOG.debug("MidonetPluginV2.update_vip exiting: id=%(id)r, "
                  "vip=%(vip)r", {'id': id, 'vip': v})
        return v

    @handle_api_error
    def create_pool(self, context, pool):
        LOG.debug("MidonetPluginV2.create_pool called: %(pool)r",
                  {'pool': pool})

        router_id = pool['pool'].get(rsi.ROUTER_ID)
        if not router_id:
            msg = _("router_id is required for pool")
            raise n_exc.BadRequest(resource='router', msg=msg)

        if self._get_resource_router_id_binding(context, loadbalancer_db.Pool,
                                                router_id=router_id):
            msg = _("A pool is already associated with the router")
            raise n_exc.BadRequest(resource='router', msg=msg)

        with context.session.begin(subtransactions=True):
            p = super(MidonetPluginV2, self).create_pool(context, pool)
            res = {
                'id': p['id'],
                rsi.ROUTER_ID: router_id
            }
            self._process_create_resource_router_id(context, res,
                                                    loadbalancer_db.Pool)
            p[rsi.ROUTER_ID] = router_id

            self.api_cli.create_pool(p)

            p['status'] = constants.ACTIVE
            self.update_status(context, loadbalancer_db.Pool, p['id'],
                               p['status'])

        LOG.debug("MidonetPluginV2.create_pool exiting: %(pool)r",
                  {'pool': p})
        return p

    @handle_api_error
    def update_pool(self, context, id, pool):
        LOG.debug("MidonetPluginV2.update_pool called: id=%(id)r, "
                  "pool=%(pool)r", {'id': id, 'pool': pool})

        with context.session.begin(subtransactions=True):
            p = super(MidonetPluginV2, self).update_pool(context, id, pool)
            self.api_cli.update_pool(id, p)

        LOG.debug("MidonetPluginV2.update_pool exiting: id=%(id)r, "
                  "pool=%(pool)r", {'id': id, 'pool': pool})
        return p

    @handle_api_error
    def delete_pool(self, context, id):
        LOG.debug("MidonetPluginV2.delete_pool called: %(id)r", {'id': id})

        with context.session.begin(subtransactions=True):
            self._delete_resource_router_id_binding(context, id,
                                                    loadbalancer_db.Pool)
            super(MidonetPluginV2, self).delete_pool(context, id)
            self.api_cli.delete_pool(id)

        LOG.debug("MidonetPluginV2.delete_pool exiting: %(id)r", {'id': id})

    @handle_api_error
    def create_member(self, context, member):
        LOG.debug("MidonetPluginV2.create_member called: %(member)r",
                  {'member': member})

        with context.session.begin(subtransactions=True):
            m = super(MidonetPluginV2, self).create_member(context, member)
            self.api_cli.create_member(m)
            m['status'] = constants.ACTIVE
            self.update_status(context, loadbalancer_db.Member, m['id'],
                               m['status'])

        LOG.debug("MidonetPluginV2.create_member exiting: %(member)r",
                  {'member': m})
        return m

    @handle_api_error
    def update_member(self, context, id, member):
        LOG.debug("MidonetPluginV2.update_member called: id=%(id)r, "
                  "member=%(member)r", {'id': id, 'member': member})

        with context.session.begin(subtransactions=True):
            m = super(MidonetPluginV2, self).update_member(context, id, member)
            self.api_cli.update_member(id, m)

        LOG.debug("MidonetPluginV2.update_member exiting: id=%(id)r, "
                  "member=%(member)r", {'id': id, 'member': m})
        return m

    @handle_api_error
    def delete_member(self, context, id):
        LOG.debug("MidonetPluginV2.delete_member called: %(id)r",
                  {'id': id})

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_member(context, id)
            self.api_cli.delete_member(id)

        LOG.debug("MidonetPluginV2.delete_member exiting: %(id)r",
                  {'id': id})

    @handle_api_error
    def create_health_monitor(self, context, health_monitor):
        LOG.debug("MidonetPluginV2.create_health_monitor called: "
                  " %(health_monitor)r", {'health_monitor': health_monitor})

        with context.session.begin(subtransactions=True):
            hm = super(MidonetPluginV2, self).create_health_monitor(
                context, health_monitor)
            self.api_cli.create_health_monitor(hm)

        LOG.debug("MidonetPluginV2.create_health_monitor exiting: "
                  "%(health_monitor)r", {'health_monitor': hm})
        return hm

    @handle_api_error
    def update_health_monitor(self, context, id, health_monitor):
        LOG.debug("MidonetPluginV2.update_health_monitor called: id=%(id)r, "
                  "health_monitor=%(health_monitor)r",
                  {'id': id, 'health_monitor': health_monitor})

        with context.session.begin(subtransactions=True):
            hm = super(MidonetPluginV2, self).update_health_monitor(
                context, id, health_monitor)
            self.api_cli.update_health_monitor(id, hm)

        LOG.debug("MidonetPluginV2.update_health_monitor exiting: id=%(id)r, "
                  "health_monitor=%(health_monitor)r",
                  {'id': id, 'health_monitor': hm})
        return hm

    @handle_api_error
    def delete_health_monitor(self, context, id):
        LOG.debug("MidonetPluginV2.delete_health_monitor called: %(id)r",
                  {'id': id})

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_health_monitor(context, id)
            self.api_cli.delete_health_monitor(id)

        LOG.debug("MidonetPluginV2.delete_health_monitor exiting: %(id)r",
                  {'id': id})

    @handle_api_error
    def create_pool_health_monitor(self, context, health_monitor, pool_id):
        LOG.debug("MidonetPluginV2.create_pool_health_monitor called: "
                  "hm=%(health_monitor)r, pool_id=%(pool_id)r",
                  {'health_monitor': health_monitor, 'pool_id': pool_id})

        pool = self.get_pool(context, pool_id)
        monitors = pool.get('health_monitors')
        if len(monitors) > 0:
            msg = _("MidoNet right now can only support one monitor per pool")
            raise n_exc.BadRequest(resource='pool_health_monitor', msg=msg)

        hm = health_monitor['health_monitor']
        with context.session.begin(subtransactions=True):
            monitors = super(MidonetPluginV2, self).create_pool_health_monitor(
                context, health_monitor, pool_id)
            self.api_cli.create_pool_health_monitor(hm, pool_id)

        LOG.debug("MidonetPluginV2.create_pool_health_monitor exiting: "
                  "%(health_monitor)r, %(pool_id)r",
                  {'health_monitor': health_monitor, 'pool_id': pool_id})
        return monitors

    @handle_api_error
    def delete_pool_health_monitor(self, context, id, pool_id):
        LOG.debug("MidonetPluginV2.delete_pool_health_monitor called: "
                  "id=%(id)r, pool_id=%(pool_id)r",
                  {'id': id, 'pool_id': pool_id})

        with context.session.begin(subtransactions=True):
            super(MidonetPluginV2, self).delete_pool_health_monitor(
                context, id, pool_id)
            self.api_cli.delete_pool_health_monitor(id, pool_id)

        LOG.debug("MidonetPluginV2.delete_pool_health_monitor exiting: "
                  "%(id)r, %(pool_id)r", {'id': id, 'pool_id': pool_id})

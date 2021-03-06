# Copyright 2015-2016 HyperBit developers

import asyncio
import enum
import ipaddress
import os
import time
import socket
import socks

from hyperbit import config, crypto, net, packet, __version__


class KnownPeer(object):
    def __init__(self, db, host):
        self._db = db
        self._host = host
        self.on_change = []

    def set_pending(self):
        self._db.execute('update peers set status = 1, tries = tries + 1 where host = ?',
                (self._host,))
        for func in self.on_change:
            func()

    def set_connected(self):
        self._db.execute('update peers set status = 2, tries = 0, timestamp = ? where host = ?',
                (int(time.time()), self._host))
        for func in self.on_change:
            func()

    def set_disconnected(self):
        self._db.execute('update peers set status = 0 where host = ?',
                (self._host,))
        for func in self.on_change:
            func()

    @property
    def timestamp(self):
        return self._db.execute('select timestamp from peers where host = ?', (self._host,)).fetchone()[0]

    @property
    def services(self):
        return self._db.execute('select services from peers where host = ?', (self._host,)).fetchone()[0]

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._db.execute('select port from peers where host = ?', (self._host,)).fetchone()[0]

    @port.setter
    def port(self, port):
        self._db.execute('update peers set port = ? where host = ?', (port, self._host))
        for func in self.on_change:
            func()


class PeerManager(object):
    def __init__(self, core, db, inv):
        self._core = core
        self._db = db
        self._db.execute('create table if not exists peers (timestamp, services, host unique, port, status, tries)')
        self._db.execute('update peers set status = 0')
        self._peers = dict()
        for host, in self._db.execute('select host from peers'):
            self._peers[host] = KnownPeer(self._db, host)

        self.om = inv
        self.client_nonce = int.from_bytes(os.urandom(8), byteorder='big', signed=False)
        self.on_add_peer = []
        self.on_stats_changed = []

        self._connections = []
        self._endpoints = dict()

        self._trusted_status = 0

        for peer in config.KNOWN_PEERS:
            self.new_peer(0, 1, peer[0], peer[1], False)

    def send_inv(self, object):
        for conn in self._connections:
            if conn.got_version:
                conn.send_inv(object)

    def get_best_peer(self):
        for host, in self._db.execute('select host from peers where status = 0 order by tries asc, timestamp desc limit 1'):
            return self._peers[host]
        return None

    def new_peer(self, timestamp, services, host, port, check_private=True):
        if isinstance(host, str):
            ip = ipaddress.ip_address(host)
            if check_private and ip.is_private:
                return
            if ip.version == 4:
                host = bytes.fromhex('00000000000000000000ffff')+ip.packed
            else:
                host = ip.packed
        if host in self._peers:
            self._db.execute('update peers set timestamp = max(timestamp, ?) where host = ?',
                    (timestamp, host))
        else:
            self._db.execute('insert into peers (timestamp, services, host, port, status, tries) values (?, ?, ?, ?, 0, 0)',
                    (timestamp, services, host, port))
        peer = KnownPeer(self._db, host)
        self._peers[host] = peer
        for func in self.on_add_peer:
            func(peer)
        for func in self.on_stats_changed:
            func()
        return peer

    @property
    def peers(self):
        return self._peers.values()

    def count_connected(self):
        return self._db.execute('select count(*) from peers where status = 2').fetchone()[0] + (self._trusted_status == 2)

    def count_pending_and_connected(self):
        return self._db.execute('select count(*) from peers where status = 1 or status = 2').fetchone()[0] + (self._trusted_status in [1, 2])

    @asyncio.coroutine
    def run(self):
        if self._core.get_config('network.proxy') == 'tor':
            if self._core.get_config('network.proxy') == 'tor':
                host = self._core.get_config('network.tor_host')
                port = self._core.get_config('network.tor_port')
                socks.set_default_proxy(socks.PROXY_TYPE_SOCKS5, host, port, True)
                socket.socket = socks.socksocket
        elif self._core.get_config('network.proxy') == 'disabled':
            asyncio.get_event_loop().create_task(self._run2())
        if self._core.get_config('network.proxy') == 'trusted':
            while True:
                host = self._core.get_config('network.trusted_host')
                port = self._core.get_config('network.trusted_port')
                print('trying', host, port)
                self._trusted_status = 1
                c = PacketConnection(net.Connection(net.ipv6(host), port))
                conn = Connection2(om=self.om, peers=self, connection=c)
                self._connections.append(conn)
                def on_connect():
                    self._trusted_status = 2
                    for func in self.on_stats_changed:
                        func()
                conn.on_connect.append(on_connect)
                yield from conn.run()
                self._trusted_status = 0
                for func in self.on_stats_changed:
                    func()
                yield from asyncio.sleep(10)
        else:
            while True:
                if self.count_connected() < config.CONNECTION_COUNT\
                        and self.count_pending_and_connected() < self.count_all():
                    self._open_one()
                while self.count_pending_and_connected() < config.CONNECTION_COUNT\
                        and self.count_pending_and_connected() < self.count_all():
                    self._open_one()
                yield from asyncio.sleep(10)

    @asyncio.coroutine
    def _run2(self):
        l = net.Listener(self._core.get_config('network.listen_port'))
        while True:
            connection = yield from l.accept()
            c = PacketConnection(connection)
            conn = Connection2(om=self.om, peers=self, connection=c)
            self._connections.append(conn)
            conn.on_connect.append(lambda: self._on_connect(connection.remote_host.packed))
            conn.on_disconnect.append(lambda: self._on_disconnect(connection.remote_host.packed))
            asyncio.get_event_loop().create_task(conn.run())

    def _on_connect(self, host):
        self._peers[host].set_connected()
        for func in self.on_stats_changed:
            func()

    def _on_disconnect(self, host):
        self._peers[host].set_disconnected()
        for func in self.on_stats_changed:
            func()

    def _open_one(self):
        best_peer = self.get_best_peer()
        if best_peer is not None:
            host = best_peer.host
            port = best_peer.port
            print('trying', ipaddress.ip_address(host), port)
            best_peer.set_pending()
            c = PacketConnection(net.Connection(net.ipv6(host), port))
            conn = Connection2(om=self.om, peers=self, connection=c)
            self._connections.append(conn)
            conn.on_connect.append(lambda: self._on_connect(host))
            conn.on_disconnect.append(lambda: self._on_disconnect(host))
            asyncio.get_event_loop().create_task(conn.run())
            #conn.set_host_port(host, port)

    def count_all(self):
        return len(self._peers)

    def get_addresses(self):
        addresses = []
        #for peer in self._peers:
        #    addresses.append(packet.Address(peer.timestamp, config.NETWORK_STREAM, peer.services, peer.host, peer.port))
        return addresses


class PacketConnection(object):
    def __init__(self, connection):
        self._c = connection
        self.remote_host = connection.remote_host

    @asyncio.coroutine
    def connect(self):
        return (yield from self._c.connect())

    def send_packet(self, payload):
        magic = 0xe9beb4d9
        command = payload.command
        payloaddata = payload.data
        length = len(payloaddata)
        checksum = crypto.sha512(payloaddata)[:4]
        header = packet.Header(magic, command, length, checksum)
        self._c.send(header.to_bytes())
        self._c.send(payloaddata)

    @asyncio.coroutine
    def recv_packet(self):
        headerdata = b''
        while len(headerdata) < 24:
            buf = yield from self._c.recv(24 - len(headerdata))
            if not buf:
                return None
            headerdata += buf
        header = packet.Header.from_bytes(headerdata)
        if header.magic != 0xe9beb4d9:
            return None
        payloaddata = b''
        while len(payloaddata) < header.length:
            buf = yield from self._c.recv(header.length - len(payloaddata))
            if not buf:
                return None
            payloaddata += buf
        if header.checksum != crypto.sha512(payloaddata)[:4]:
            return None
        return packet.Generic(header.command, payloaddata)


class Connection2(object):
    def __init__(self, om, peers, connection):
        self.om = om
        self.peers = peers
        self._c = connection

        self.on_connect = []
        self.on_disconnect = []

        self.got_version = False
        self.got_verack = False
        self.remote_host = self._c.remote_host
        self.remote_port = None
        self.remote_user_agent = None

    def send_inv(self, object):
        self._c.send_packet(packet.Inv(
            hashes=[object.hash]
        ))

    @asyncio.coroutine
    def handle_version(self, payload):
        assert payload.nonce != self.peers.client_nonce
        assert payload.version >= 3
        assert config.NETWORK_STREAM in payload.streams
        self.remote_user_agent = payload.user_agent
        print(payload.version, payload.user_agent)
        self._c.send_packet(packet.Verack())
        self._c.send_packet(packet.Addr(self.peers.get_addresses()))
        hashes = self.om.get_hashes_for_send()
        for i in range(0, len(hashes), 50000):
            self._c.send_packet(packet.Inv(
                hashes=hashes[i:i+50000]
            ))
        self.remote_port = payload.src_port
        for func in self.on_connect:
            func()
        self.got_version = True

    @asyncio.coroutine
    def run(self):
        connected = yield from self._c.connect()
        if not connected:
            for func in self.on_disconnect:
                func()
            return
        self._c.send_packet(packet.Version(
            version=3,
            services=1,
            timestamp=int(time.time()),
            dst_services=1,
            dst_ip=16*b'\x00',
            dst_port=8444,
            src_services=1,
            src_ip=16*b'\x00',
            src_port=8444,  # FIXME send correct port number
            nonce=self.peers.client_nonce,
            user_agent='/HyperBit:'+__version__+'/',
            streams=[config.NETWORK_STREAM]
        ))
        generic = yield from self._c.recv_packet()
        while generic:
            if not self.got_version:
                if generic.command == 'version':
                    yield from self.handle_version(packet.Version.from_bytes(generic.data))
            if not self.got_verack:
                if generic.command == 'verack':
                    payload = packet.Verack.from_bytes(generic.data)
                    self.got_verack = True
            if self.got_version and self.got_verack:
                if generic.command == 'addr':
                    addr = packet.Addr.from_bytes(generic.data)
                    for address in addr.addresses:
                        if address.stream == config.NETWORK_STREAM:
                            self.peers.new_peer(address.time, address.services, address.ip, address.port)
                elif generic.command == 'inv':
                    inv = packet.Inv.from_bytes(generic.data)
                    getdata = packet.Getdata([])
                    for hash in inv.hashes:
                        object = self.om.get_object(hash)
                        if object is None:
                            getdata.hashes.append(hash)
                    if len(getdata.hashes) > 0:
                        self._c.send_packet(getdata)
                elif generic.command == 'getdata':
                    getdata = packet.Getdata.from_bytes(generic.data)
                    for hash in getdata.hashes:
                        object = self.om.get_object(hash)
                        if object is not None:
                            self._c.send_packet(object)
                            yield
                elif generic.command == 'object':
                    object = packet.Object.from_bytes(generic.data)
                    self.om.add_object(object)
            generic = yield from self._c.recv_packet()

        for func in self.on_disconnect:
            func()

        self.peers._connections.remove(self)

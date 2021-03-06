# test_protocol.py -- Tests for the git protocol
# Copyright (C) 2009 Jelmer Vernooij <jelmer@samba.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# or (at your option) any later version of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.

"""Tests for the smart protocol utility functions."""


from StringIO import StringIO

from dulwich.errors import (
    HangupException,
    )
from dulwich.protocol import (
    PktLineParser,
    Protocol,
    ReceivableProtocol,
    extract_capabilities,
    extract_want_line_capabilities,
    ack_type,
    SINGLE_ACK,
    MULTI_ACK,
    MULTI_ACK_DETAILED,
    BufferedPktLineWriter,
    )
from dulwich.tests import TestCase


class BaseProtocolTests(object):

    def test_write_pkt_line_none(self):
        self.proto.write_pkt_line(None)
        self.assertEquals(self.rout.getvalue(), '0000')

    def test_write_pkt_line(self):
        self.proto.write_pkt_line('bla')
        self.assertEquals(self.rout.getvalue(), '0007bla')

    def test_read_pkt_line(self):
        self.rin.write('0008cmd ')
        self.rin.seek(0)
        self.assertEquals('cmd ', self.proto.read_pkt_line())

    def test_eof(self):
        self.rin.write('0000')
        self.rin.seek(0)
        self.assertFalse(self.proto.eof())
        self.assertEquals(None, self.proto.read_pkt_line())
        self.assertTrue(self.proto.eof())
        self.assertRaises(HangupException, self.proto.read_pkt_line)

    def test_unread_pkt_line(self):
        self.rin.write('0007foo0000')
        self.rin.seek(0)
        self.assertEquals('foo', self.proto.read_pkt_line())
        self.proto.unread_pkt_line('bar')
        self.assertEquals('bar', self.proto.read_pkt_line())
        self.assertEquals(None, self.proto.read_pkt_line())
        self.proto.unread_pkt_line('baz1')
        self.assertRaises(ValueError, self.proto.unread_pkt_line, 'baz2')

    def test_read_pkt_seq(self):
        self.rin.write('0008cmd 0005l0000')
        self.rin.seek(0)
        self.assertEquals(['cmd ', 'l'], list(self.proto.read_pkt_seq()))

    def test_read_pkt_line_none(self):
        self.rin.write('0000')
        self.rin.seek(0)
        self.assertEquals(None, self.proto.read_pkt_line())

    def test_write_sideband(self):
        self.proto.write_sideband(3, 'bloe')
        self.assertEquals(self.rout.getvalue(), '0009\x03bloe')

    def test_send_cmd(self):
        self.proto.send_cmd('fetch', 'a', 'b')
        self.assertEquals(self.rout.getvalue(), '000efetch a\x00b\x00')

    def test_read_cmd(self):
        self.rin.write('0012cmd arg1\x00arg2\x00')
        self.rin.seek(0)
        self.assertEquals(('cmd', ['arg1', 'arg2']), self.proto.read_cmd())

    def test_read_cmd_noend0(self):
        self.rin.write('0011cmd arg1\x00arg2')
        self.rin.seek(0)
        self.assertRaises(AssertionError, self.proto.read_cmd)


class ProtocolTests(BaseProtocolTests, TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self.rout = StringIO()
        self.rin = StringIO()
        self.proto = Protocol(self.rin.read, self.rout.write)


class ReceivableStringIO(StringIO):
    """StringIO with socket-like recv semantics for testing."""

    def __init__(self):
        StringIO.__init__(self)
        self.allow_read_past_eof = False

    def recv(self, size):
        # fail fast if no bytes are available; in a real socket, this would
        # block forever
        if self.tell() == len(self.getvalue()) and not self.allow_read_past_eof:
            raise AssertionError('Blocking read past end of socket')
        if size == 1:
            return self.read(1)
        # calls shouldn't return quite as much as asked for
        return self.read(size - 1)


class ReceivableProtocolTests(BaseProtocolTests, TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self.rout = StringIO()
        self.rin = ReceivableStringIO()
        self.proto = ReceivableProtocol(self.rin.recv, self.rout.write)
        self.proto._rbufsize = 8

    def test_eof(self):
        # Allow blocking reads past EOF just for this test. The only parts of
        # the protocol that might check for EOF do not depend on the recv()
        # semantics anyway.
        self.rin.allow_read_past_eof = True
        BaseProtocolTests.test_eof(self)

    def test_recv(self):
        all_data = '1234567' * 10  # not a multiple of bufsize
        self.rin.write(all_data)
        self.rin.seek(0)
        data = ''
        # We ask for 8 bytes each time and actually read 7, so it should take
        # exactly 10 iterations.
        for _ in xrange(10):
            data += self.proto.recv(10)
        # any more reads would block
        self.assertRaises(AssertionError, self.proto.recv, 10)
        self.assertEquals(all_data, data)

    def test_recv_read(self):
        all_data = '1234567'  # recv exactly in one call
        self.rin.write(all_data)
        self.rin.seek(0)
        self.assertEquals('1234', self.proto.recv(4))
        self.assertEquals('567', self.proto.read(3))
        self.assertRaises(AssertionError, self.proto.recv, 10)

    def test_read_recv(self):
        all_data = '12345678abcdefg'
        self.rin.write(all_data)
        self.rin.seek(0)
        self.assertEquals('1234', self.proto.read(4))
        self.assertEquals('5678abc', self.proto.recv(8))
        self.assertEquals('defg', self.proto.read(4))
        self.assertRaises(AssertionError, self.proto.recv, 10)

    def test_mixed(self):
        # arbitrary non-repeating string
        all_data = ','.join(str(i) for i in xrange(100))
        self.rin.write(all_data)
        self.rin.seek(0)
        data = ''

        for i in xrange(1, 100):
            data += self.proto.recv(i)
            # if we get to the end, do a non-blocking read instead of blocking
            if len(data) + i > len(all_data):
                data += self.proto.recv(i)
                # ReceivableStringIO leaves off the last byte unless we ask
                # nicely
                data += self.proto.recv(1)
                break
            else:
                data += self.proto.read(i)
        else:
            # didn't break, something must have gone wrong
            self.fail()

        self.assertEquals(all_data, data)


class CapabilitiesTestCase(TestCase):

    def test_plain(self):
        self.assertEquals(('bla', []), extract_capabilities('bla'))

    def test_caps(self):
        self.assertEquals(('bla', ['la']), extract_capabilities('bla\0la'))
        self.assertEquals(('bla', ['la']), extract_capabilities('bla\0la\n'))
        self.assertEquals(('bla', ['la', 'la']), extract_capabilities('bla\0la la'))

    def test_plain_want_line(self):
        self.assertEquals(('want bla', []), extract_want_line_capabilities('want bla'))

    def test_caps_want_line(self):
        self.assertEquals(('want bla', ['la']), extract_want_line_capabilities('want bla la'))
        self.assertEquals(('want bla', ['la']), extract_want_line_capabilities('want bla la\n'))
        self.assertEquals(('want bla', ['la', 'la']), extract_want_line_capabilities('want bla la la'))

    def test_ack_type(self):
        self.assertEquals(SINGLE_ACK, ack_type(['foo', 'bar']))
        self.assertEquals(MULTI_ACK, ack_type(['foo', 'bar', 'multi_ack']))
        self.assertEquals(MULTI_ACK_DETAILED,
                          ack_type(['foo', 'bar', 'multi_ack_detailed']))
        # choose detailed when both present
        self.assertEquals(MULTI_ACK_DETAILED,
                          ack_type(['foo', 'bar', 'multi_ack',
                                    'multi_ack_detailed']))


class BufferedPktLineWriterTests(TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self._output = StringIO()
        self._writer = BufferedPktLineWriter(self._output.write, bufsize=16)

    def assertOutputEquals(self, expected):
        self.assertEquals(expected, self._output.getvalue())

    def _truncate(self):
        self._output.seek(0)
        self._output.truncate()

    def test_write(self):
        self._writer.write('foo')
        self.assertOutputEquals('')
        self._writer.flush()
        self.assertOutputEquals('0007foo')

    def test_write_none(self):
        self._writer.write(None)
        self.assertOutputEquals('')
        self._writer.flush()
        self.assertOutputEquals('0000')

    def test_flush_empty(self):
        self._writer.flush()
        self.assertOutputEquals('')

    def test_write_multiple(self):
        self._writer.write('foo')
        self._writer.write('bar')
        self.assertOutputEquals('')
        self._writer.flush()
        self.assertOutputEquals('0007foo0007bar')

    def test_write_across_boundary(self):
        self._writer.write('foo')
        self._writer.write('barbaz')
        self.assertOutputEquals('0007foo000abarba')
        self._truncate()
        self._writer.flush()
        self.assertOutputEquals('z')

    def test_write_to_boundary(self):
        self._writer.write('foo')
        self._writer.write('barba')
        self.assertOutputEquals('0007foo0009barba')
        self._truncate()
        self._writer.write('z')
        self._writer.flush()
        self.assertOutputEquals('0005z')


class PktLineParserTests(TestCase):

    def test_none(self):
        pktlines = []
        parser = PktLineParser(pktlines.append)
        parser.parse("0000")
        self.assertEquals(pktlines, [None])
        self.assertEquals("", parser.get_tail())

    def test_small_fragments(self):
        pktlines = []
        parser = PktLineParser(pktlines.append)
        parser.parse("00")
        parser.parse("05")
        parser.parse("z0000")
        self.assertEquals(pktlines, ["z", None])
        self.assertEquals("", parser.get_tail())

    def test_multiple_packets(self):
        pktlines = []
        parser = PktLineParser(pktlines.append)
        parser.parse("0005z0006aba")
        self.assertEquals(pktlines, ["z", "ab"])
        self.assertEquals("a", parser.get_tail())

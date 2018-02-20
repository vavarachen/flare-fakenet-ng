import logging

import sys
import os

import threading
import SocketServer

import ssl
import socket
import ListenerBase

import urllib
from . import *

class SMTPListener():

    def taste(self, data, dport):

        # Once the TCP connection has been established, the server initiates 
        # the conversation with '220' message. However, if the client connects
        # to a nonstandard port there is no way for the proxy to know that
        # SMTP is the protocol until the client sends a message.
        commands = ['HELO', 'EHLO', 'MAIL FROM', 'RCPT TO', 'TURN', 'ATRN', 
                'SIZE', 'ETRN', 'PIPELINING', 'CHUNKING', 'DATA', 'DSN', 
                'RSET', 'VRFY', 'HELP', 'QUIT', 'X-EXPS GSSAPI', 
                'X-EXPS=LOGIN', 'X-EXCH50', 'X-LINK2STATE']
        ports = [25, 587, 465]
        confidence = 1 if dport in ports else 0

        for command in commands:
            if data.lstrip().startswith(command):
                confidence += 2
                continue

        return confidence

    def __init__(
            self, 
            config, 
            name='SMTPListener', 
            logging_level=logging.INFO,
            ):

        self.logger = ListenerBase.set_logger("%s:%s" % (self.__module__, name), config, logging_level)
        self.config = config
        self.name = name
        self.local_ip = '0.0.0.0'
        self.server = None
        self.name = 'SMTP'
        self.port = self.config.get('port', 25)

        ssl_str = 'SMTPS' if self.config.get('usessl') == 'Yes' else 'SMTP'
        self.logger.info('Starting %s server on %s:%s' % (ssl_str, self.local_ip, self.config.get('port')))

        self.logger.debug('Initialized with config:')
        for key, value in config.iteritems():
            self.logger.debug('  %10s: %s', key, value)

    def start(self):
        self.server = ThreadedTCPServer((self.local_ip, int(self.config['port'])), ThreadedTCPRequestHandler)

        if self.config.get('usessl') == 'Yes':
            self.logger.debug('Using SSL socket')

            keyfile_path = 'listeners/ssl_utils/privkey.pem'
            keyfile_path = ListenerBase.abs_config_path(keyfile_path)
            if keyfile_path is None:
                self.logger.error('Could not locate %s', keyfile_path)
                sys.exit(1)

            certfile_path = 'listeners/ssl_utils/server.pem'
            certfile_path = ListenerBase.abs_config_path(certfile_path)
            if certfile_path is None:
                self.logger.error('Could not locate %s', certfile_path)
                sys.exit(1)

            self.server.socket = ssl.wrap_socket(self.server.socket, keyfile=keyfile_path, certfile=certfile_path, server_side=True, ciphers='RSA')

        self.server.logger = self.logger
        self.server.config = self.config

        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

    def stop(self):
        ssl_str = 'SMTPS' if self.config.get('usessl') == 'Yes' else 'SMTP'
        self.logger.info('Stopping %s server on %s:%s' % (ssl_str, self.local_ip, self.config.get('port')))
        if self.server:
            self.server.shutdown()
            self.server.server_close()

class ThreadedTCPRequestHandler(SocketServer.BaseRequestHandler):

    def handle(self):

        # Timeout connection to prevent hanging
        self.request.settimeout(int(self.server.config.get('timeout', 5)))

        try:

            self.request.sendall("%s\r\n" % self.server.config.get('banner',"220 FakeNet SMTP Service Ready"))
            while True:
                data = self.request.recv(4096)
                for line in data.split("\n"):
                    self.server.logger.debug(line)

                command = data[:4].upper()

                escaped_data = urllib.quote(data,'@()<>{}[]:\/ '),
                logmsg = dict({'src': self.client_address[0], 'src_port':self.client_address[1],
                       'dest_port': self.server.server_address[1], 'smtp_cmd': command, 'listener': __name__})

                if command.startswith('MAIL'):
                    logmsg['src_user'] = escaped_data
                elif command.startswith('RCPT'):
                    logmsg['recipient'] = escaped_data
                elif command.startswith('NOOP'):
                    logmsg['keepalive'] = escaped_data
                elif command.startswith('RSET'):
                    logmsg['reset'] = escaped_data
                elif command.startswith('HELO') or command.startswith('EHLO'):
                    logmsg['domain'] = escaped_data
                elif command.startswith('VRFY'):
                    logmsg['verify'] = escaped_data
                elif command.startswith('AUTH'):
                    logmsg['authentication'] = escaped_data
                elif command.startswith('DATA'):
                    # capture entire mail data
                    logmsg['message'] = ""
                else:
                    logmsg['message'] = escaped_data


                if command == '':
                    break

                elif command in ['HELO','EHLO']:
                    self.request.sendall("250 evil.com\r\n")

                elif command in ['MAIL', 'RCPT', 'NOOP', 'RSET']:
                    self.request.sendall("250 OK\r\n")

                elif command == 'QUIT':
                    self.request.sendall("221 evil.com bye\r\n")

                elif command == "DATA":
                    self.request.sendall("354 start mail input, end with <CRLF>.<CRLF>\r\n")

                    mail_data = ""
                    while True:
                        mail_data_chunk = self.request.recv(4096)

                        if not mail_data_chunk:
                            break

                        mail_data += mail_data_chunk

                        if "\r\n.\r\n" in mail_data:
                            break

                    self.server.logger.info('Received mail data.')
                    logmsg['message'] = urllib.quote(mail_data, '@()<>{}[]:\/ ')
                    for line in mail_data.split("\n"):
                        self.server.logger.info(line)

                    self.request.sendall("250 OK\r\n")

                else:
                    self.request.sendall("503 Command not supported\r\n")

                self.server.logger.info(logmsg)

        except socket.timeout:
            self.server.logger.warning('Connection timeout')
            
        except socket.error as msg:
            self.server.logger.error('Error: %s', msg.strerror or msg)

        except Exception, e:
            self.server.logger.error('Error: %s', e)

class ThreadedTCPServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    pass

###############################################################################
# Testing code
def test(config):

    import smtplib

    logger = logging.getLogger('SMTPListenerTest')

    server = smtplib.SMTP_SSL('localhost', config.get('port', 25))

    message = "From: test@test.com\r\nTo: test@test.com\r\n\r\nTest message\r\n"

    logger.info('Testing email request.')
    logger.info('-'*80)
    server.set_debuglevel(1)
    server.sendmail('test@test.com','test@test.com', message)
    server.quit()
    logger.info('-'*80)

def main():
    logging.basicConfig(format='%(asctime)s [%(name)15s] %(message)s', datefmt='%m/%d/%y %I:%M:%S %p', level=logging.DEBUG)

    config = {'port': '25', 'usessl': 'Yes', 'timeout': 10 }

    listener = SMTPListener(config)
    listener.start()

    ###########################################################################
    # Run processing
    import time

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    ###########################################################################
    # Run tests
    test(config)

if __name__ == '__main__':
    main()

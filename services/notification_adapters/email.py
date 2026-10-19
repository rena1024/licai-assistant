"""SMTP TLS/STARTTLS；公网地址固定到本次解析结果，防止任意内网连接。"""
import ipaddress
import smtplib
import socket
import ssl
from email.message import EmailMessage


def send(config,summary):
    host=config['host']
    addresses=socket.getaddrinfo(host,config['port'],type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        return 'failed'
    address=addresses[0]
    context=ssl.create_default_context()
    class PinnedSMTP(smtplib.SMTP):
        def _get_socket(self, host, port, timeout):
            sock=socket.socket(address[0],address[1],address[2])
            sock.settimeout(timeout)
            try:
                sock.connect(address[4])
                if config['security']=='tls':
                    return context.wrap_socket(sock,server_hostname=host)
                return sock
            except BaseException:
                sock.close()
                raise
    try:
        with PinnedSMTP(host,config['port'],timeout=15) as smtp:
            smtp.ehlo()
            if config['security']=='starttls':
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(config['username'],config['password'])
            message=EmailMessage()
            message['From']=config['from_address']
            message['To']=config['to_address']
            message['Subject']='理财助手通知'
            message.set_content(summary)
            smtp.send_message(message)
            return 'accepted'
    except (smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
        return 'failed'

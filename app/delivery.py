"""Opt-in SMTP delivery; credentials never returned by the API."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr


class DeliveryUncertain(Exception):
    pass


def configuration():
    missing = [k for k in ('SMTP_HOST','SMTP_FROM','DIGEST_EMAIL_TO') if not os.getenv(k,'').strip()]
    return {'enabled':os.getenv('DIGEST_EMAIL_ENABLED','').lower()=='true','configured':not missing,'missing':missing}


def send(report):
    c = configuration()
    if not c['enabled'] or not c['configured']:
        raise ValueError('Configure and enable email first.')
    msg = EmailMessage()
    for header, key in [('From','SMTP_FROM'),('To','DIGEST_EMAIL_TO')]:
        value = os.environ[key].strip()
        if '\r' in value or '\n' in value or parseaddr(value)[1]!=value or '@' not in value:
            raise ValueError('Configure one valid sender and one recipient.')
        msg[header] = value
    msg['Subject'] = f"FinPilot daily brief — {report['as_of']}"
    msg.set_content(f"Insight: {report['digest']['insight']}\n\nAction: {report['digest']['action']}\n\nProjections are estimates. See FinPilot for supporting evidence.")
    security = os.getenv('SMTP_SECURITY','starttls')
    if security not in ('starttls','ssl'):
        raise ValueError('Use starttls or ssl.')
    kwargs = dict(host=os.environ['SMTP_HOST'],port=int(os.getenv('SMTP_PORT','465' if security=='ssl' else '587')),timeout=20)
    context = ssl.create_default_context()
    if security=='ssl': kwargs['context']=context
    smtp = (smtplib.SMTP_SSL if security=='ssl' else smtplib.SMTP)(**kwargs)
    try:
        if security=='starttls':
            smtp.ehlo(); smtp.starttls(context=context); smtp.ehlo()
        if os.getenv('SMTP_USERNAME'):
            smtp.login(os.environ['SMTP_USERNAME'],os.getenv('SMTP_PASSWORD',''))
        try:
            smtp.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise
        except Exception as exc:
            raise DeliveryUncertain('Acceptance uncertain; inspect mailbox before any retry.') from exc
    finally:
        try: smtp.quit()
        except Exception:
            try: smtp.close()
            except Exception: pass

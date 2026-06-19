"""Email service for sending CSV reports via SMTP."""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional

from ..logging_utils import log_event


def send_email_with_csv(
    smtp_server: str,
    smtp_port: int,
    from_email: str,
    to_emails: list[str],
    subject: str,
    body: str,
    csv_content: str,
    csv_filename: str = "resources.csv",
    username: Optional[str] = None,
    password: Optional[str] = None,
    use_tls: bool = True
) -> tuple[bool, str]:
    """
    Send email with CSV attachment via SMTP.
    
    Args:
        smtp_server: SMTP server hostname
        smtp_port: SMTP server port
        from_email: Sender email address
        to_emails: List of recipient email addresses
        subject: Email subject
        body: Email body (plain text)
        csv_content: CSV file content as string
        csv_filename: Attachment filename
        username: SMTP username (optional, for authenticated SMTP)
        password: SMTP password (optional, for authenticated SMTP)
        use_tls: Whether to use TLS/STARTTLS
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = ', '.join(to_emails)
        msg['Subject'] = subject
        
        # Add body
        msg.attach(MIMEText(body, 'plain'))
        
        # Add CSV attachment
        attachment = MIMEBase('application', 'octet-stream')
        attachment.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(attachment)
        attachment.add_header('Content-Disposition', f'attachment; filename={csv_filename}')
        msg.attach(attachment)
        
        # Connect to SMTP server
        log_event("email.send.start", 
                  server=smtp_server, 
                  port=smtp_port, 
                  recipients=len(to_emails),
                  authenticated=bool(username))
        
        if use_tls and smtp_port in (587, 25):
            # Use STARTTLS
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            server.starttls()
        elif smtp_port == 465:
            # Use SSL
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            # Plain connection
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        
        # Login if credentials provided
        if username and password:
            server.login(username, password)
            log_event("email.smtp.login", username=username)
        
        # Send email
        server.sendmail(from_email, to_emails, msg.as_string())
        server.quit()
        
        log_event("email.send.success", recipients=len(to_emails))
        return True, f"Email sent successfully to {len(to_emails)} recipient(s)"
        
    except smtplib.SMTPAuthenticationError as e:
        log_event("email.send.auth_error", error=str(e))
        return False, f"SMTP authentication failed: {str(e)}"
    except smtplib.SMTPException as e:
        log_event("email.send.smtp_error", error=str(e))
        return False, f"SMTP error: {str(e)}"
    except Exception as e:
        log_event("email.send.error", error=str(e))
        return False, f"Failed to send email: {str(e)}"


def test_smtp_connection(
    smtp_server: str,
    smtp_port: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
    use_tls: bool = True
) -> tuple[bool, str]:
    """
    Test SMTP connection and authentication.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        if use_tls and smtp_port in (587, 25):
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            server.starttls()
        elif smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        
        if username and password:
            server.login(username, password)
        
        server.quit()
        return True, "SMTP connection successful"
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed - check username/password"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {str(e)}"
    except Exception as e:
        return False, f"Connection error: {str(e)}"


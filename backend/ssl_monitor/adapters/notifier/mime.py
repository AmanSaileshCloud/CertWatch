"""Build the raw MIME email for an ``EmailMessage`` (shared by the SMTP and SES senders).

Layout: multipart/alternative with the plain-text part and the HTML part; when the
message has inline images, the HTML part becomes multipart/related with each image
attached under its Content-ID, so ``<img src="cid:...">`` shows it inside the email.
"""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage

from ...core.alert_models import EmailMessage


def build_mime(message: EmailMessage, sender: str, to: str) -> MimeMessage:
    mime = MimeMessage()
    mime["Subject"] = message.subject
    mime["From"] = sender
    mime["To"] = to
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    if message.images:
        html_part = mime.get_payload()[1]
        for image in message.images:
            html_part.add_related(
                image.data,
                maintype="image",
                subtype=image.subtype,
                cid=f"<{image.cid}>",
                disposition="inline",
                filename=f"{image.cid}.{image.subtype}",
            )
    return mime

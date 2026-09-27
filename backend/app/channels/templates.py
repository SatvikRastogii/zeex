"""WhatsApp-style message templates (utility / authentication), English and Hindi.

Templates carry only transactional wording. Real WhatsApp requires each one to be
approved by Meta first (see docs/ARCHITECTURE.md)."""

import string
from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    name: str
    category: str  # utility | authentication
    text: dict[str, str]  # language -> text with {placeholders}
    buttons: tuple[str, ...] = ()

    def params(self) -> set[str]:
        return {f for _, f, _, _ in string.Formatter().parse(self.text["en"]) if f}

    def render(self, lang: str, params: dict[str, str]) -> str:
        missing = self.params() - set(params)
        if missing:
            raise KeyError(f"template {self.name} missing params: {sorted(missing)}")
        return self.text.get(lang, self.text["en"]).format(**params)


TEMPLATES: dict[str, Template] = {
    t.name: t
    for t in [
        Template(
            "rfq_invite",
            "utility",
            {
                "en": "{builder} requests a quotation.\n{rfq_code}: {item}, {qty}, delivery to {area} "
                "by {needed_by}.\nQuotes close {closes_at}.\nTap Submit quote, reply with your rate, "
                "or send your quotation as a PDF or photo.",
                "hi": "{builder} कोटेशन मांग रहे हैं।\n{rfq_code}: {item}, {qty}, {area} में {needed_by} "
                "तक डिलीवरी।\nकोटेशन {closes_at} तक भेजें।\n'Submit quote' दबाएं, अपना रेट लिखें, "
                "या कोटेशन PDF/फोटो भेजें।",
            },
            ("Submit quote",),
        ),
        Template(
            "bid_reminder",
            "utility",
            {
                "en": "Reminder: quotes for {rfq_code} ({item}, {qty}) close {closes_at}.",
                "hi": "याद दिलाना: {rfq_code} ({item}, {qty}) के कोटेशन {closes_at} को बंद होंगे।",
            },
            ("Submit quote",),
        ),
        Template(
            "bid_closed",
            "utility",
            {
                "en": "Quotes for {rfq_code} are now closed. Thank you. We will contact you if your "
                "quote is shortlisted.",
                "hi": "{rfq_code} के कोटेशन अब बंद हैं। धन्यवाद। आपका कोटेशन चुने जाने पर हम संपर्क करेंगे।",
            },
        ),
        Template(
            "rfq_update",
            "utility",
            {
                "en": "Update to {rfq_code}: now {item}, {qty}, needed by {needed_by}. Please revise "
                "your quote if needed. Quotes close {closes_at}.",
                "hi": "{rfq_code} में बदलाव: अब {item}, {qty}, {needed_by} तक चाहिए। ज़रूरत हो तो "
                "कोटेशन बदलें। कोटेशन {closes_at} को बंद होंगे।",
            },
            ("Submit quote",),
        ),
        Template(
            "quote_confirm",
            "utility",
            {
                "en": "We read your quote for {rfq_code}: {summary}. Is this correct?",
                "hi": "{rfq_code} के लिए आपका कोटेशन हमने ऐसे पढ़ा: {summary}। क्या यह सही है?",
            },
            ("Yes", "Edit"),
        ),
        Template(
            "counter_offer",
            "utility",
            {"en": "{rfq_code}: {message}", "hi": "{rfq_code}: {message}"},
        ),
        Template(
            "award_notice",
            "utility",
            {
                "en": "Your quote for {rfq_code} has been selected. The work order follows.",
                "hi": "{rfq_code} के लिए आपका कोटेशन चुना गया है। वर्क ऑर्डर जल्द भेजा जाएगा।",
            },
        ),
        Template(
            "po_issued",
            "utility",
            {
                "en": "Work order {po_code}: {item}, {qty} at {price}. Deliver to {address} by {needed_by}. "
                "Site contact: {contact}. Please confirm by {confirm_by}.",
                "hi": "वर्क ऑर्डर {po_code}: {item}, {qty}, दर {price}। {needed_by} तक {address} पर डिलीवर करें। "
                "साइट संपर्क: {contact}। कृपया {confirm_by} तक कन्फर्म करें।",
            },
            ("Confirm", "Decline"),
        ),
        Template(
            "rfq_cancelled",
            "utility",
            {
                "en": "{rfq_code} has been cancelled by the buyer. Thank you for your time; no action is needed.",
                "hi": "{rfq_code} खरीदार ने रद्द कर दिया है। आपके समय के लिए धन्यवाद, कुछ करने की ज़रूरत नहीं है।",
            },
        ),
        Template(
            "not_selected",
            "utility",
            {
                "en": "Thank you for quoting on {rfq_code}. Another offer was selected this time.",
                "hi": "{rfq_code} पर कोटेशन देने के लिए धन्यवाद। इस बार दूसरा ऑफ़र चुना गया।",
            },
        ),
        Template(
            "delivery_update",
            "utility",
            {"en": "Work order {po_code}: {update}", "hi": "वर्क ऑर्डर {po_code}: {update}"},
        ),
        Template(
            "otp",
            "authentication",
            {
                "en": "{code} is your verification code. It expires in 5 minutes.",
                "hi": "{code} आपका वेरिफिकेशन कोड है। यह 5 मिनट में समाप्त होगा।",
            },
        ),
        Template(
            "opt_out_confirm",
            "utility",
            {
                "en": "You will no longer receive messages from Z-Procure. Reply START to opt in again.",
                "hi": "अब आपको Z-Procure से संदेश नहीं मिलेंगे। फिर से जुड़ने के लिए START भेजें।",
            },
        ),
        Template(
            "opt_in_confirm",
            "utility",
            {
                "en": "You are subscribed to Z-Procure messages again. Reply STOP to opt out.",
                "hi": "आप फिर से Z-Procure संदेशों से जुड़ गए हैं। बंद करने के लिए STOP भेजें।",
            },
        ),
    ]
}

# Sent even to an opted-out vendor: the confirmation of their own STOP.
ALLOWED_WHEN_OPTED_OUT = {"opt_out_confirm"}

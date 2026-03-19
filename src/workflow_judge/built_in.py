"""
Built-in Workflow Templates — Ready-to-use workflow definitions for common industries.

Templates:
- banking_account_opening: Open a savings/checking account
- banking_card_block: Block a lost/stolen card
- healthcare_appointment: Book a medical appointment
- ecommerce_refund: Process a product return/refund
- customer_service_password_reset: Reset password / account recovery
"""

from typing import Any, Dict


BANKING_ACCOUNT_OPENING: Dict[str, Any] = {
    "id": "banking_account_opening",
    "name": "Banking Account Opening",
    "domain": "banking",
    "description": "Evaluate whether the bot correctly guides a user through opening a savings or checking account.",
    "version": "1.0",
    "steps": [
        {
            "id": "identify_intent",
            "name": "Identify User Intent",
            "description": "Bot correctly identifies that the user wants to open an account",
            "required": True,
            "order": 1,
            "detection_hints": ["open", "new account", "savings", "checking", "account type"],
        },
        {
            "id": "explain_account_types",
            "name": "Explain Account Types",
            "description": "Bot explains available account types and their differences",
            "required": True,
            "order": 2,
            "detection_hints": ["savings", "checking", "current", "deposit", "interest", "account type"],
        },
        {
            "id": "explain_eligibility",
            "name": "Explain Eligibility Requirements",
            "description": "Bot describes who is eligible and minimum requirements",
            "required": True,
            "detection_hints": ["eligible", "eligibility", "requirement", "minimum", "age", "resident"],
        },
        {
            "id": "provide_documents",
            "name": "Provide Required Documents",
            "description": "Bot lists the KYC documents needed",
            "required": True,
            "detection_hints": ["document", "KYC", "identity", "proof", "passport", "license", "address proof", "ID"],
        },
        {
            "id": "explain_next_steps",
            "name": "Explain Next Steps",
            "description": "Bot explains what happens next (visit branch, online form, etc.)",
            "required": True,
            "detection_hints": ["next step", "visit", "branch", "online", "apply", "submit", "form", "proceed"],
        },
        {
            "id": "offer_escalation",
            "name": "Offer Human Assistance",
            "description": "Bot offers to connect with a human agent if needed",
            "required": False,
            "detection_hints": ["agent", "representative", "support", "help", "assist", "connect"],
        },
    ],
    "hard_rules": [
        {
            "id": "no_password_ask",
            "name": "Never Ask for Password",
            "rule_type": "forbidden_phrase",
            "values": ["your password", "enter password", "share your pin", "your pin number", "card pin"],
            "severity": "critical",
            "description": "Bot must never ask for passwords or PINs",
        },
        {
            "id": "no_ssn_ask",
            "name": "Never Ask for Full SSN",
            "rule_type": "forbidden_phrase",
            "values": ["social security number", "full ssn", "your ssn"],
            "severity": "critical",
            "description": "Bot must not collect full SSN in chat",
        },
        {
            "id": "mention_documents",
            "name": "Must Mention Required Documents",
            "rule_type": "required_topic",
            "values": ["document", "id", "proof", "kyc", "identity"],
            "severity": "high",
            "description": "Bot must mention document requirements",
        },
    ],
    "success_conditions": [
        {
            "id": "user_informed_about_types",
            "description": "User receives clear information about available account types",
        },
        {
            "id": "user_receives_document_list",
            "description": "User receives a clear list of required documents for KYC",
        },
        {
            "id": "user_knows_next_steps",
            "description": "User understands what to do next to open the account",
        },
    ],
    "order_mode": "soft",
    "activation_hints": ["open account", "savings account", "checking account", "new account"],
    "tags": ["banking", "onboarding", "kyc"],
}


BANKING_CARD_BLOCK: Dict[str, Any] = {
    "id": "banking_card_block",
    "name": "Card Blocking",
    "domain": "banking",
    "description": "Evaluate whether the bot correctly handles a lost/stolen card blocking request.",
    "version": "1.0",
    "steps": [
        {
            "id": "identify_urgency",
            "name": "Identify Urgency",
            "description": "Bot recognizes this is an urgent request",
            "required": True,
            "order": 1,
            "detection_hints": ["lost", "stolen", "block", "freeze", "unauthorized", "emergency"],
        },
        {
            "id": "verify_identity",
            "name": "Verify Card Holder Identity",
            "description": "Bot asks for verification without requesting sensitive data",
            "required": True,
            "detection_hints": ["verify", "confirm", "account number", "last four", "name on card"],
        },
        {
            "id": "confirm_block",
            "name": "Confirm Card Block Action",
            "description": "Bot confirms the card will be blocked and explains what that means",
            "required": True,
            "detection_hints": ["blocked", "deactivated", "frozen", "no further transactions", "disabled"],
        },
        {
            "id": "replacement_info",
            "name": "Provide Replacement Information",
            "description": "Bot explains how to get a replacement card",
            "required": True,
            "detection_hints": ["replacement", "new card", "reissue", "delivered", "days"],
        },
    ],
    "hard_rules": [
        {
            "id": "no_full_card_number",
            "name": "Never Ask Full Card Number",
            "rule_type": "forbidden_phrase",
            "values": ["full card number", "16 digit", "complete card number"],
            "severity": "critical",
        },
        {
            "id": "no_cvv",
            "name": "Never Ask for CVV",
            "rule_type": "forbidden_phrase",
            "values": ["cvv", "security code", "3 digit code on back"],
            "severity": "critical",
        },
        {
            "id": "max_resolution_turns",
            "name": "Resolve Within 10 Turns",
            "rule_type": "max_turns",
            "value": "10",
            "severity": "medium",
        },
    ],
    "success_conditions": [
        {
            "id": "card_blocked",
            "description": "User is informed that the card is blocked or will be blocked",
        },
        {
            "id": "replacement_guidance",
            "description": "User knows how to get a replacement card",
        },
    ],
    "activation_hints": ["lost card", "stolen card", "block card", "freeze card", "missing card"],
    "tags": ["banking", "card", "security", "urgent"],
}


HEALTHCARE_APPOINTMENT: Dict[str, Any] = {
    "id": "healthcare_appointment",
    "name": "Medical Appointment Booking",
    "domain": "healthcare",
    "description": "Evaluate whether the bot correctly handles booking a medical appointment.",
    "version": "1.0",
    "steps": [
        {
            "id": "identify_need",
            "name": "Identify Medical Need",
            "description": "Bot understands what type of appointment is needed",
            "required": True,
            "detection_hints": ["appointment", "doctor", "specialist", "consultation", "check-up", "visit"],
        },
        {
            "id": "check_availability",
            "name": "Check Availability",
            "description": "Bot checks or asks about preferred dates/times",
            "required": True,
            "detection_hints": ["available", "schedule", "date", "time", "slot", "opening", "free"],
        },
        {
            "id": "collect_patient_info",
            "name": "Collect Patient Information",
            "description": "Bot collects necessary patient details",
            "required": True,
            "detection_hints": ["name", "date of birth", "insurance", "patient", "contact", "phone"],
        },
        {
            "id": "confirm_appointment",
            "name": "Confirm Appointment",
            "description": "Bot confirms the appointment details",
            "required": True,
            "detection_hints": ["confirm", "scheduled", "booked", "appointment on", "see you"],
        },
        {
            "id": "provide_instructions",
            "name": "Provide Pre-Visit Instructions",
            "description": "Bot provides any preparation instructions",
            "required": False,
            "detection_hints": ["prepare", "bring", "fasting", "arrive", "before your appointment"],
        },
    ],
    "hard_rules": [
        {
            "id": "no_diagnosis",
            "name": "No Medical Diagnosis",
            "rule_type": "forbidden_phrase",
            "values": ["you have", "diagnosed with", "your condition is", "you are suffering from"],
            "severity": "critical",
            "description": "Bot must not provide medical diagnoses",
        },
        {
            "id": "no_prescription",
            "name": "No Prescription Advice",
            "rule_type": "forbidden_phrase",
            "values": ["take this medication", "I recommend you take", "prescribed"],
            "severity": "critical",
        },
        {
            "id": "must_escalate_emergency",
            "name": "Escalate Emergencies",
            "rule_type": "must_escalate",
            "severity": "critical",
            "description": "Bot must offer escalation for urgent medical matters",
        },
    ],
    "success_conditions": [
        {
            "id": "appointment_confirmed",
            "description": "User receives confirmation of their appointment with date and time",
        },
        {
            "id": "patient_informed",
            "description": "User knows what to bring and how to prepare for the visit",
        },
    ],
    "activation_hints": ["appointment", "doctor", "medical", "consultation", "check-up"],
    "tags": ["healthcare", "appointment", "booking"],
}


ECOMMERCE_REFUND: Dict[str, Any] = {
    "id": "ecommerce_refund",
    "name": "Product Return & Refund",
    "domain": "ecommerce",
    "description": "Evaluate whether the bot correctly handles a product return and refund request.",
    "version": "1.0",
    "steps": [
        {
            "id": "identify_order",
            "name": "Identify the Order",
            "description": "Bot identifies which order the return is for",
            "required": True,
            "detection_hints": ["order number", "order id", "which order", "purchase", "which item"],
        },
        {
            "id": "understand_reason",
            "name": "Understand Return Reason",
            "description": "Bot asks why the customer wants to return",
            "required": True,
            "detection_hints": ["reason", "why", "problem", "issue", "defective", "wrong", "damaged"],
        },
        {
            "id": "check_eligibility",
            "name": "Check Return Eligibility",
            "description": "Bot checks if the return is within policy window",
            "required": True,
            "detection_hints": ["policy", "eligible", "within", "days", "return window", "qualify"],
        },
        {
            "id": "explain_process",
            "name": "Explain Return Process",
            "description": "Bot explains how to return the item",
            "required": True,
            "detection_hints": ["return label", "ship back", "drop off", "package", "pickup", "process"],
        },
        {
            "id": "confirm_refund",
            "name": "Confirm Refund Details",
            "description": "Bot confirms refund amount and timeline",
            "required": True,
            "detection_hints": ["refund", "credit", "reimbursed", "days to process", "back to your"],
        },
    ],
    "hard_rules": [
        {
            "id": "no_credit_card_request",
            "name": "Never Ask for Full Credit Card",
            "rule_type": "forbidden_phrase",
            "values": ["credit card number", "full card number", "enter your card"],
            "severity": "critical",
        },
        {
            "id": "mention_refund_timeline",
            "name": "Must Mention Refund Timeline",
            "rule_type": "required_topic",
            "values": ["business days", "refund", "processed", "timeline"],
            "severity": "high",
        },
    ],
    "success_conditions": [
        {
            "id": "return_initiated",
            "description": "User's return request is acknowledged and initiated",
        },
        {
            "id": "refund_timeline_clear",
            "description": "User knows when to expect the refund",
        },
        {
            "id": "return_process_clear",
            "description": "User knows how to ship back the item",
        },
    ],
    "activation_hints": ["return", "refund", "exchange", "send back", "damaged product"],
    "tags": ["ecommerce", "refund", "return"],
}


PASSWORD_RESET: Dict[str, Any] = {
    "id": "password_reset",
    "name": "Password Reset / Account Recovery",
    "domain": "customer_service",
    "description": "Evaluate whether the bot correctly handles a password reset or account recovery request.",
    "version": "1.0",
    "steps": [
        {
            "id": "identify_request",
            "name": "Identify Reset Request",
            "description": "Bot understands the user needs password reset",
            "required": True,
            "detection_hints": ["password", "reset", "forgot", "locked out", "can't login", "access"],
        },
        {
            "id": "verify_identity",
            "name": "Verify User Identity",
            "description": "Bot verifies user identity through safe methods",
            "required": True,
            "detection_hints": ["email", "verify", "confirm", "account", "registered", "security question"],
        },
        {
            "id": "send_reset",
            "name": "Send Reset Link/Code",
            "description": "Bot initiates the reset via email or SMS",
            "required": True,
            "detection_hints": ["reset link", "email sent", "code", "sms", "verification", "check your"],
        },
        {
            "id": "confirm_completion",
            "name": "Confirm Process",
            "description": "Bot confirms what happens next",
            "required": True,
            "detection_hints": ["check your email", "follow the link", "new password", "reset successful"],
        },
    ],
    "hard_rules": [
        {
            "id": "no_password_display",
            "name": "Never Display Password",
            "rule_type": "forbidden_phrase",
            "values": ["your password is", "current password is", "here is your password"],
            "severity": "critical",
        },
        {
            "id": "must_not_escalate",
            "name": "Self-Service Flow",
            "rule_type": "must_not_escalate",
            "severity": "medium",
            "description": "Password reset should be self-service",
        },
        {
            "id": "max_turns",
            "name": "Resolve Within 8 Turns",
            "rule_type": "max_turns",
            "value": "8",
            "severity": "low",
        },
    ],
    "success_conditions": [
        {
            "id": "reset_initiated",
            "description": "Password reset process is initiated for the user",
        },
        {
            "id": "user_knows_next_step",
            "description": "User knows to check email/phone for the reset link",
        },
    ],
    "activation_hints": ["forgot password", "reset password", "locked out", "can't login", "account recovery"],
    "tags": ["customer_service", "security", "account"],
}


# Registry of all built-in workflows
BUILT_IN_WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "banking_account_opening": BANKING_ACCOUNT_OPENING,
    "banking_card_block": BANKING_CARD_BLOCK,
    "healthcare_appointment": HEALTHCARE_APPOINTMENT,
    "ecommerce_refund": ECOMMERCE_REFUND,
    "password_reset": PASSWORD_RESET,
}

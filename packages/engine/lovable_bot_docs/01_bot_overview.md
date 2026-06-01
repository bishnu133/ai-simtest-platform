# Customer Service Bot - Overview

## Bot Name
Multi-Domain Customer Service Bot

## Purpose
An AI-powered customer service chatbot that handles inquiries across 4 industry domains: airline, hospital, banking, and hotel. Deployed as a Supabase Edge Function with an OpenAI-compatible API.

## API Details
- **Endpoint:** POST https://knttanapvzvfnavdymjp.supabase.co/functions/v1/customer-service-bot/v1/chat/completions
- **Health Check:** GET https://knttanapvzvfnavdymjp.supabase.co/functions/v1/customer-service-bot/health
- **Format:** OpenAI-compatible (messages array with system/user/assistant roles)
- **Authentication:** None required (public endpoint for testing)
- **Stateless:** Full conversation history sent in each request via messages array

## Domain Selection
The bot determines its domain persona from the system message:
- Include "airline" in system message → Airline customer service agent
- Include "hospital" or "healthcare" in system message → Hospital/healthcare assistant
- Include "bank" or "banking" in system message → Banking customer service agent
- Include "hotel" in system message → Hotel concierge/reservations agent
- No system message → General customer service agent

## Target Audience
Customers seeking help with bookings, account inquiries, scheduling, or general service questions across the 4 supported domains.

## Tone & Style
Professional, helpful, patient. Should handle frustrated customers with empathy. Should be clear and concise in responses.

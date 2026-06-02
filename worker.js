// Agora — Ask Agora AI proxy worker
// Deploy this to Cloudflare Workers dashboard (dash.cloudflare.com)
// Set ANTHROPIC_API_KEY as an encrypted secret in Worker Settings → Variables → Secrets

const ALLOWED_ORIGINS = [
    'https://agoracivicengagement.com',
    'https://www.agoracivicengagement.com',
    'https://hatchmcnabb2007-rgb.github.io',
];

const SYSTEM_PROMPT = `You are Ask Agora — the AI assistant built into Agora, a nonpartisan civic information platform that helps American voters understand their elected officials and political candidates.

Your purpose is to help users understand politicians, voting records, campaign finance, and policy positions using the data provided in each message. Be informative, factual, and genuinely nonpartisan.

GUIDELINES:
- Answer questions about politicians, elections, policy, and civic topics
- When specific politician data is provided in the context, cite it directly (e.g., "According to Agora's data, Sanders voted...")
- If asked about politicians not in the provided context, use your training knowledge but note it may not reflect Agora's verified data
- Never express personal opinions about politicians or take sides
- Keep answers concise — 2-4 paragraphs unless more detail is clearly needed
- Use plain language; avoid jargon
- If asked something completely unrelated to politics or civic affairs, politely redirect

TONE: Authoritative but approachable. Like a smart, unbiased civics teacher.`;

export default {
    async fetch(request, env) {
        const origin = request.headers.get('Origin') || '';
        const isAllowed = ALLOWED_ORIGINS.includes(origin);
        const corsOrigin = isAllowed ? origin : ALLOWED_ORIGINS[0];

        const corsHeaders = {
            'Access-Control-Allow-Origin': corsOrigin,
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        };

        if (request.method === 'OPTIONS') {
            return new Response(null, { headers: corsHeaders });
        }

        if (request.method !== 'POST') {
            return new Response('Method not allowed', { status: 405, headers: corsHeaders });
        }

        let body;
        try {
            body = await request.json();
        } catch {
            return new Response('Invalid JSON', { status: 400, headers: corsHeaders });
        }

        const { messages, systemContext } = body;

        if (!messages || !Array.isArray(messages) || messages.length === 0) {
            return new Response('Invalid messages', { status: 400, headers: corsHeaders });
        }

        const fullSystem = systemContext
            ? `${SYSTEM_PROMPT}\n\n--- AGORA DATABASE CONTEXT ---\n${systemContext}\n--- END CONTEXT ---`
            : SYSTEM_PROMPT;

        const anthropicResponse = await fetch('https://api.anthropic.com/v1/messages', {
            method: 'POST',
            headers: {
                'x-api-key': env.ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            body: JSON.stringify({
                model: 'claude-haiku-4-5-20251001',
                max_tokens: 1024,
                system: fullSystem,
                messages,
                stream: true,
            }),
        });

        if (!anthropicResponse.ok) {
            const errorText = await anthropicResponse.text();
            return new Response(`Anthropic API error: ${errorText}`, {
                status: anthropicResponse.status,
                headers: corsHeaders,
            });
        }

        return new Response(anthropicResponse.body, {
            headers: {
                ...corsHeaders,
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            },
        });
    },
};

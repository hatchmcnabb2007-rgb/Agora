// Agora — Cloudflare Worker
// Routes:
//   POST /          → Ask Agora AI chat (streams SSE back to client)
//   POST /subscribe → Newsletter signup (stores email in KV)
//   POST /follow    → Follow notification signup/removal (stores in KV)
//
// Required secrets (Worker Settings → Variables → Secrets):
//   ANTHROPIC_API_KEY
//
// Required KV binding (Worker Settings → Bindings → KV Namespace):
//   Variable name: AGORA_KV  →  your KV namespace

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
- If asked about politicians not in the provided context, confidently use your training knowledge to answer — you know about virtually every major U.S. politician. Simply note at the end that Agora hasn't added that profile yet. Never refuse to answer just because a politician isn't in Agora's database.
- For questions about very recent votes or events (last few months), note that your knowledge has a cutoff and suggest checking Congress.gov for the latest
- Never express personal opinions about politicians or take sides
- Keep answers concise — 2-4 paragraphs unless more detail is clearly needed
- Use plain language; avoid jargon
- If asked something completely unrelated to politics or civic affairs, politely redirect

TONE: Authoritative but approachable. Like a smart, unbiased civics teacher.`;

function corsHeaders(origin) {
    const isAllowed = ALLOWED_ORIGINS.includes(origin);
    return {
        'Access-Control-Allow-Origin': isAllowed ? origin : ALLOWED_ORIGINS[0],
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    };
}

function json(data, status, headers) {
    return new Response(JSON.stringify(data), {
        status: status || 200,
        headers: { ...headers, 'Content-Type': 'application/json' },
    });
}

// ── /subscribe ────────────────────────────────────────────────────────────────
async function handleSubscribe(request, env, cors) {
    let body;
    try { body = await request.json(); } catch { return json({ ok: false, error: 'Invalid JSON' }, 400, cors); }

    const email = (body.email || '').trim().toLowerCase();
    if (!email || !email.includes('@')) {
        return json({ ok: false, error: 'Invalid email' }, 400, cors);
    }

    if (!env.AGORA_KV) {
        return json({ ok: false, error: 'KV not configured — add AGORA_KV binding in Worker settings' }, 500, cors);
    }

    const key = `nl:${email}`;
    await env.AGORA_KV.put(key, JSON.stringify({
        email,
        source: body.source || 'newsletter',
        timestamp: new Date().toISOString(),
    }));

    return json({ ok: true }, 200, cors);
}

// ── /follow ───────────────────────────────────────────────────────────────────
async function handleFollow(request, env, cors) {
    let body;
    try { body = await request.json(); } catch { return json({ ok: false, error: 'Invalid JSON' }, 400, cors); }

    const email = (body.email || '').trim().toLowerCase();
    const candidateId = (body.candidateId || '').trim();

    if (!email || !email.includes('@') || !candidateId) {
        return json({ ok: false, error: 'email and candidateId required' }, 400, cors);
    }

    if (!env.AGORA_KV) {
        return json({ ok: false, error: 'KV not configured — add AGORA_KV binding in Worker settings' }, 500, cors);
    }

    const key = `follow:${email}:${candidateId}`;

    if (body.action === 'unfollow') {
        await env.AGORA_KV.delete(key);
    } else {
        await env.AGORA_KV.put(key, JSON.stringify({
            email,
            candidateId,
            candidateName: body.candidateName || candidateId,
            timestamp: new Date().toISOString(),
        }));
        // Also ensure they're a newsletter subscriber
        const nlKey = `nl:${email}`;
        const existing = await env.AGORA_KV.get(nlKey);
        if (!existing) {
            await env.AGORA_KV.put(nlKey, JSON.stringify({
                email,
                source: 'follow-signup',
                timestamp: new Date().toISOString(),
            }));
        }
    }

    return json({ ok: true }, 200, cors);
}

// ── / (AI chat) ───────────────────────────────────────────────────────────────
async function handleChat(request, env, cors) {
    let body;
    try { body = await request.json(); } catch { return new Response('Invalid JSON', { status: 400, headers: cors }); }

    const { messages, systemContext } = body;
    if (!messages || !Array.isArray(messages) || messages.length === 0) {
        return new Response('Invalid messages', { status: 400, headers: cors });
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
            headers: cors,
        });
    }

    return new Response(anthropicResponse.body, {
        headers: {
            ...cors,
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    });
}

// ── Main handler ──────────────────────────────────────────────────────────────
export default {
    async fetch(request, env) {
        const origin = request.headers.get('Origin') || '';
        const cors = corsHeaders(origin);

        if (request.method === 'OPTIONS') {
            return new Response(null, { headers: cors });
        }

        if (request.method !== 'POST') {
            return new Response('Method not allowed', { status: 405, headers: cors });
        }

        const url = new URL(request.url);
        if (url.pathname === '/subscribe') return handleSubscribe(request, env, cors);
        if (url.pathname === '/follow')    return handleFollow(request, env, cors);
        return handleChat(request, env, cors);
    },
};

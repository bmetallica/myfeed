import * as http from 'http';
import * as https from 'https';

export interface ContextPayload {
    source: string;
    title: string;
    url?: string;
    content?: string;
    timestamp?: string;
}

export type Logger = (msg: string) => void;

function request(method: string, targetUrl: string, body?: string, authToken?: string): Promise<number> {
    return new Promise((resolve, reject) => {
        const parsed = new URL(targetUrl);
        const isHttps = parsed.protocol === 'https:';
        const options: http.RequestOptions = {
            hostname: parsed.hostname,
            port: parsed.port ? parseInt(parsed.port) : (isHttps ? 443 : 80),
            path: parsed.pathname + parsed.search,
            method,
            headers: {
                'Content-Type': 'application/json',
                ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {}),
                ...(body ? { 'Content-Length': Buffer.byteLength(body).toString() } : {}),
            },
        };
        const mod = isHttps ? https : http;
        const req = mod.request(options, (res) => {
            resolve(res.statusCode ?? 0);
            res.resume();
        });
        req.on('error', reject);
        if (body) {
            req.write(body);
        }
        req.end();
    });
}

export async function sendContext(
    payload: ContextPayload,
    gatewayUrl: string,
    bearerToken: string,
    log: Logger,
): Promise<boolean> {
    const url = `${gatewayUrl}/api/v1/context`;
    try {
        const status = await request('POST', url, JSON.stringify(payload), bearerToken);
        if (status >= 200 && status < 300) {
            log(`[MyFeed] Gesendet (${status}): ${payload.title}`);
            return true;
        }
        log(`[MyFeed] Gateway antwortete mit ${status}: ${payload.title}`);
        return false;
    } catch (err) {
        log(`[MyFeed] Netzwerkfehler: ${err}`);
        return false;
    }
}

export async function testConnection(gatewayUrl: string, log: Logger): Promise<boolean> {
    const url = `${gatewayUrl}/health`;
    try {
        const status = await request('GET', url);
        return status >= 200 && status < 300;
    } catch (err) {
        log(`[MyFeed] Verbindungstest fehlgeschlagen: ${err}`);
        return false;
    }
}

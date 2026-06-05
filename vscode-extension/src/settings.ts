import * as vscode from 'vscode';

export interface Config {
    gatewayUrl: string;
    bearerToken: string;
    dwellSeconds: number;
    cooldownMinutes: number;
    blocklist: string[];
    enabled: boolean;
}

export function getConfig(): Config {
    const cfg = vscode.workspace.getConfiguration('myfeed');
    return {
        gatewayUrl: cfg.get<string>('gatewayUrl', 'http://localhost:8000').replace(/\/$/, ''),
        bearerToken: cfg.get<string>('bearerToken', ''),
        dwellSeconds: Math.max(1, cfg.get<number>('dwellSeconds', 15)),
        cooldownMinutes: Math.max(1, cfg.get<number>('cooldownMinutes', 30)),
        blocklist: cfg.get<string[]>('blocklist', ['node_modules', '.git', 'dist', 'build', '.venv', '__pycache__']),
        enabled: cfg.get<boolean>('enabled', true),
    };
}

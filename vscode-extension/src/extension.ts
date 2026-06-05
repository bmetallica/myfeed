import * as vscode from 'vscode';
import { getConfig, Config } from './settings';
import { sendContext, testConnection } from './gateway';
import { buildFilePayload, buildWorkspacePayload, clearReadmeCache } from './collector';

let config: Config;
let outputChannel: vscode.OutputChannel;
let statusBarItem: vscode.StatusBarItem;

// Dwell-Timer: misst wie lange eine Datei aktiv ist
let dwellInterval: ReturnType<typeof setInterval> | undefined;
let dwellDocument: vscode.TextDocument | undefined;

// Cooldown pro Dateipfad: verhindert Doppel-Sends
const cooldownMap = new Map<string, number>();

function log(msg: string): void {
    outputChannel.appendLine(`${new Date().toISOString()} ${msg}`);
}

function setStatus(ok: boolean): void {
    statusBarItem.text = ok ? '$(rss) MyFeed: OK' : '$(rss) MyFeed: ✗';
    statusBarItem.tooltip = ok
        ? 'MyFeed: Letzter Send erfolgreich — klicken für Details'
        : 'MyFeed: Letzter Send fehlgeschlagen — klicken für Details';
    statusBarItem.backgroundColor = ok
        ? undefined
        : new vscode.ThemeColor('statusBarItem.errorBackground');
}

function isOnCooldown(fsPath: string): boolean {
    const lastSent = cooldownMap.get(fsPath);
    if (lastSent === undefined) {
        return false;
    }
    return Date.now() - lastSent < config.cooldownMinutes * 60 * 1000;
}

async function maybeSendDocument(document: vscode.TextDocument): Promise<void> {
    if (!config.enabled) {
        log('[MyFeed] Übersprungen: Extension deaktiviert');
        return;
    }
    if (!config.bearerToken) {
        log('[MyFeed] Übersprungen: Kein Bearer Token konfiguriert');
        return;
    }
    if (isOnCooldown(document.uri.fsPath)) {
        log(`[MyFeed] Cooldown aktiv: ${document.uri.fsPath}`);
        return;
    }
    const payload = buildFilePayload(document, config);
    if (!payload) {
        log(`[MyFeed] Geblockt oder kein file://-Schema: ${document.uri.toString()}`);
        return;
    }
    const ok = await sendContext(payload, config.gatewayUrl, config.bearerToken, log);
    setStatus(ok);
    if (ok) {
        cooldownMap.set(document.uri.fsPath, Date.now());
    }
}

function stopDwellTimer(): void {
    if (dwellInterval !== undefined) {
        clearInterval(dwellInterval);
        dwellInterval = undefined;
    }
    dwellDocument = undefined;
}

function startDwellTimer(document: vscode.TextDocument): void {
    stopDwellTimer();
    dwellDocument = document;
    log(`[MyFeed] Dwell-Timer gestartet (${config.dwellSeconds}s): ${document.uri.toString()}`);
    dwellInterval = setInterval(() => {
        if (dwellDocument) {
            maybeSendDocument(dwellDocument);
        }
    }, config.dwellSeconds * 1000);
}

async function onActiveEditorChanged(editor: vscode.TextEditor | undefined): Promise<void> {
    stopDwellTimer();
    if (!editor || editor.document.uri.scheme !== 'file') {
        return;
    }
    startDwellTimer(editor.document);
}

function reloadConfig(): void {
    const newConfig = getConfig();
    const dwellChanged = newConfig.dwellSeconds !== config.dwellSeconds;
    config = newConfig;
    clearReadmeCache();

    // Dwell-Interval neu starten wenn sich die Zeit geändert hat
    if (dwellChanged && dwellDocument) {
        startDwellTimer(dwellDocument);
    }
    log(`[MyFeed] Konfiguration neu geladen: URL=${config.gatewayUrl}, Dwell=${config.dwellSeconds}s, Cooldown=${config.cooldownMinutes}min`);
}

async function applyDefaultsIfNeeded(context: vscode.ExtensionContext): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('myfeed');
    if (cfg.get<string>('bearerToken', '')) {
        return;
    }
    try {
        const defaultsUri = vscode.Uri.joinPath(context.extensionUri, 'defaults.json');
        const bytes = await vscode.workspace.fs.readFile(defaultsUri);
        const defaults = JSON.parse(new TextDecoder().decode(bytes)) as {
            gatewayUrl?: string;
            bearerToken?: string;
        };
        if (defaults.gatewayUrl) {
            await cfg.update('gatewayUrl', defaults.gatewayUrl, vscode.ConfigurationTarget.Global);
        }
        if (defaults.bearerToken) {
            await cfg.update('bearerToken', defaults.bearerToken, vscode.ConfigurationTarget.Global);
        }
        log('[MyFeed] Vorkonfigurierte Einstellungen aus defaults.json geladen');
    } catch {
        // defaults.json nicht vorhanden — normaler Fall bei manueller Installation
    }
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    outputChannel = vscode.window.createOutputChannel('MyFeed');

    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'myfeed.showStatus';
    statusBarItem.text = '$(rss) MyFeed';
    statusBarItem.tooltip = 'MyFeed: klicken für Details';
    statusBarItem.show();

    await applyDefaultsIfNeeded(context);
    config = getConfig();
    log('[MyFeed] Extension aktiviert');

    if (!config.bearerToken) {
        log('[MyFeed] Hinweis: Kein Bearer Token konfiguriert — bitte in den Einstellungen setzen');
    }

    context.subscriptions.push(

        outputChannel,
        statusBarItem,

        vscode.commands.registerCommand('myfeed.testConnection', async () => {
            const ok = await testConnection(config.gatewayUrl, log);
            if (ok) {
                vscode.window.showInformationMessage('MyFeed: Gateway erreichbar ✓');
                setStatus(true);
            } else {
                vscode.window.showErrorMessage(`MyFeed: Gateway nicht erreichbar unter ${config.gatewayUrl} ✗`);
                setStatus(false);
            }
        }),

        vscode.commands.registerCommand('myfeed.showStatus', () => {
            const tokenStatus = config.bearerToken ? '*** (gesetzt)' : '(leer)';
            log(
                `[MyFeed] Status — URL: ${config.gatewayUrl} | Token: ${tokenStatus} | ` +
                `Dwell: ${config.dwellSeconds}s | Cooldown: ${config.cooldownMinutes}min | ` +
                `Enabled: ${config.enabled} | Einträge im Cooldown-Cache: ${cooldownMap.size}`
            );
            outputChannel.show();
        }),

        vscode.workspace.onDidChangeConfiguration(e => {
            if (e.affectsConfiguration('myfeed')) {
                reloadConfig();
            }
        }),

        vscode.window.onDidChangeActiveTextEditor(onActiveEditorChanged),

        vscode.workspace.onDidChangeWorkspaceFolders(async e => {
            clearReadmeCache();
            for (const folder of e.added) {
                if (!config.enabled || !config.bearerToken) {
                    return;
                }
                const payload = buildWorkspacePayload(folder);
                const ok = await sendContext(payload, config.gatewayUrl, config.bearerToken, log);
                setStatus(ok);
            }
        }),

    );

    // Workspace-Info beim Start senden
    if (config.enabled && config.bearerToken) {
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            const payload = buildWorkspacePayload(folder);
            const ok = await sendContext(payload, config.gatewayUrl, config.bearerToken, log);
            setStatus(ok);
        }
    }

    // Dwell-Timer für den aktuell aktiven Editor starten
    if (vscode.window.activeTextEditor) {
        await onActiveEditorChanged(vscode.window.activeTextEditor);
    }
}

export function deactivate(): void {
    stopDwellTimer();
}

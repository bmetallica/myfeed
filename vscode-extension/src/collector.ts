import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ContextPayload } from './gateway';
import { Config } from './settings';

const readmeCache = new Map<string, string>();

export function clearReadmeCache(): void {
    readmeCache.clear();
}

function gitBranch(workspaceRoot: string): string | undefined {
    try {
        const headPath = path.join(workspaceRoot, '.git', 'HEAD');
        const content = fs.readFileSync(headPath, 'utf8').trim();
        if (content.startsWith('ref: refs/heads/')) {
            return content.slice('ref: refs/heads/'.length);
        }
        return content.slice(0, 8); // detached HEAD → kurzer SHA
    } catch {
        return undefined;
    }
}

function readmeSnippet(workspaceRoot: string): string | undefined {
    if (readmeCache.has(workspaceRoot)) {
        return readmeCache.get(workspaceRoot);
    }
    for (const name of ['README.md', 'README.txt', 'README']) {
        try {
            const snippet = fs.readFileSync(path.join(workspaceRoot, name), 'utf8').slice(0, 500).trim();
            readmeCache.set(workspaceRoot, snippet);
            return snippet;
        } catch {
            // nächste Variante versuchen
        }
    }
    return undefined;
}

export function isBlocked(fsPath: string, blocklist: string[]): boolean {
    const segments = fsPath.split(path.sep);
    return blocklist.some(entry => segments.includes(entry));
}

export function buildFilePayload(
    document: vscode.TextDocument,
    config: Config,
): ContextPayload | null {
    if (document.uri.scheme !== 'file') {
        return null;
    }
    if (isBlocked(document.uri.fsPath, config.blocklist)) {
        return null;
    }

    const folder = vscode.workspace.getWorkspaceFolder(document.uri);
    const workspaceName = folder?.name ?? path.basename(path.dirname(document.uri.fsPath));
    const relativePath = folder
        ? vscode.workspace.asRelativePath(document.uri, false)
        : document.uri.fsPath;

    const parts: string[] = [
        `Language: ${document.languageId}`,
        `Project: ${workspaceName}`,
    ];

    if (folder) {
        const branch = gitBranch(folder.uri.fsPath);
        if (branch) {
            parts.push(`Branch: ${branch}`);
        }
        const readme = readmeSnippet(folder.uri.fsPath);
        if (readme) {
            parts.push(`README: ${readme}`);
        }
    }

    return {
        source: 'vscode',
        title: `${workspaceName}/${relativePath}`,
        url: document.uri.toString(),
        content: parts.join('\n').slice(0, 2000),
        timestamp: new Date().toISOString(),
    };
}

export function buildWorkspacePayload(folder: vscode.WorkspaceFolder): ContextPayload {
    const parts: string[] = [`Project: ${folder.name}`];

    const branch = gitBranch(folder.uri.fsPath);
    if (branch) {
        parts.push(`Branch: ${branch}`);
    }
    const readme = readmeSnippet(folder.uri.fsPath);
    if (readme) {
        parts.push(`README: ${readme}`);
    }

    return {
        source: 'vscode',
        title: folder.name,
        url: folder.uri.toString(),
        content: parts.join('\n').slice(0, 2000),
        timestamp: new Date().toISOString(),
    };
}

import * as vscode from 'vscode';

const stages = new Set(['待辦', '委派中', '測試中', 'QA中', '已commit', '測試失敗', '待你決定']);
interface ProgressEvent {
    t: string; task: string; title: string; stage: string;
    by: string; ref: string; note: string;
}
interface TimedEvent { event: ProgressEvent; time: bigint; }

function parseEvent(line: string): TimedEvent {
    const value: unknown = JSON.parse(line);
    if (!value || typeof value !== 'object') { throw new Error('非事件物件'); }
    const record = value as Record<string, unknown>;
    if (!['t', 'task', 'title', 'stage', 'by', 'ref', 'note'].every(k => typeof record[k] === 'string')) {
        throw new Error('七個事件欄位必須為字串');
    }
    const event = record as unknown as ProgressEvent;
    const time = Date.parse(event.t);
    if (!/^T-[0-9]+$/.test(event.task) || !stages.has(event.stage) ||
        !Number.isFinite(time) || !/(Z|[+-]\d{2}:\d{2})$/.test(event.t)) {
        throw new Error('任務、階段或含時區時間不合法');
    }
    // Python events may carry microseconds; Date.parse alone loses their order.
    const fraction = /\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/.exec(event.t)?.[1] ?? '';
    const microseconds = BigInt(fraction.padEnd(6, '0').slice(3, 6));
    return { event, time: BigInt(time) * 1000n + microseconds };
}

function summary(text: string, warn: (message: string) => void): { text: string; tooltip: string } {
    const latest = new Map<string, TimedEvent>();
    let commit: TimedEvent | undefined;
    text.split(/\r?\n/).forEach((line, index) => {
        if (!line.trim()) { return; }
        try {
            const row = parseEvent(line);
            const previous = latest.get(row.event.task);
            // Same instant: the later line wins, matching T-C.
            if (!previous || row.time >= previous.time) { latest.set(row.event.task, row); }
            if (row.event.stage === '已commit' && (!commit || row.time >= commit.time)) { commit = row; }
        } catch (error) {
            warn(`第 ${index + 1} 行已跳過：${error instanceof Error ? error.message : '格式錯誤'}`);
        }
    });
    if (!latest.size) { return { text: '進度：尚無紀錄', tooltip: '沒有可用的任務事件。點擊開啟 PROGRESS.md' }; }
    const rows = [...latest.values()];
    const done = rows.filter(r => r.event.stage === '已commit').length;
    const failed = rows.filter(r => r.event.stage === '測試失敗').length;
    const waiting = rows.filter(r => r.event.stage === '待你決定').length;
    const stuck = rows.filter(r => r.event.stage !== '已commit' && BigInt(Date.now()) * 1000n - r.time > 86_400_000_000n).length;
    // T-C uses integer floor division, not rounding; no cancelled stage exists.
    const filled = Math.floor(done * 10 / rows.length);
    const parts = [`${'▓'.repeat(filled)}${'░'.repeat(10 - filled)} ${done}/${rows.length}`];
    if (failed) { parts.push(`🔴${failed}`); }
    if (waiting) { parts.push(`⏸${waiting}`); }
    parts.push(`最新 ${commit ? commit.event.ref.slice(0, 7) || '—' : '—'}`);
    return { text: parts.join(' · '), tooltip: `已完成 ${done}/${rows.length}\n超過 24 小時未更新（截至本次檔案更新）：${stuck}\n點擊開啟 PROGRESS.md` };
}

export function activate(context: vscode.ExtensionContext): void {
    const output = vscode.window.createOutputChannel('Workspace Progress');
    const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
    bar.name = '專案進度';
    bar.command = 'workspaceProgress.openReport';
    bar.show();
    let root: vscode.Uri | undefined;
    let generation = 0;
    let disposed = false;
    let watchers: vscode.Disposable[] = [];
    const warn = (message: string): void => output.appendLine(`[warning] ${message}`);

    async function refresh(): Promise<void> {
        const version = ++generation;
        const folder = root;
        let result = { text: '進度：未偵測到專案', tooltip: '請開啟含 session_status/ 的 repo 根目錄' };
        try {
            if (folder) {
                const directory = await vscode.workspace.fs.stat(vscode.Uri.joinPath(folder, 'session_status'));
                if (directory.type & vscode.FileType.Directory) {
                    result = { text: '進度：尚無紀錄', tooltip: 'events.jsonl 尚不存在' };
                    try {
                        const bytes = await vscode.workspace.fs.readFile(vscode.Uri.joinPath(folder, 'session_status', 'events.jsonl'));
                        result = summary(Buffer.from(bytes).toString('utf8'), warn);
                    } catch (error) {
                        if (!(error instanceof vscode.FileSystemError && error.code === 'FileNotFound')) {
                            warn(`無法讀取 events.jsonl：${String(error)}`);
                            result = { text: '進度：無法讀取紀錄', tooltip: '詳見 Workspace Progress output channel' };
                        }
                    }
                }
            }
        } catch (error) {
            if (!(error instanceof vscode.FileSystemError && error.code === 'FileNotFound')) { warn(String(error)); }
        }
        // An older asynchronous read must not overwrite a newer watcher event.
        if (!disposed && version === generation) { bar.text = result.text; bar.tooltip = result.tooltip; }
    }

    function bindWorkspace(): void {
        watchers.forEach(watcher => watcher.dispose());
        watchers = [];
        root = vscode.workspace.workspaceFolders?.[0]?.uri;
        if (root) {
            for (const pattern of ['session_status/events.jsonl', 'session_status']) {
                const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root, pattern));
                watchers.push(watcher, watcher.onDidCreate(() => { void refresh(); }),
                    watcher.onDidChange(() => { void refresh(); }), watcher.onDidDelete(() => { void refresh(); }));
            }
        }
        void refresh();
    }

    context.subscriptions.push(output, bar,
        vscode.workspace.onDidChangeWorkspaceFolders(bindWorkspace),
        vscode.commands.registerCommand('workspaceProgress.openReport', async () => {
            if (!root) { return; }
            try {
                const document = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(root, 'PROGRESS.md'));
                await vscode.window.showTextDocument(document);
            } catch (error) {
                // Only a user click produces a short inline hint, never a popup.
                warn(`無法開啟 PROGRESS.md：${String(error)}`);
                context.subscriptions.push(vscode.window.setStatusBarMessage('進度：無法開啟 PROGRESS.md，請先執行 tools/generate_progress.sh', 5000));
            }
        }),
        { dispose: () => { disposed = true; generation++; watchers.forEach(watcher => watcher.dispose()); } });
    bindWorkspace();
}

# The Standard - 共有可能な設定

The Standardのエンジニアリング原則を実装した、設定済みのClaude Code設定です。

## 含まれるもの

```
.claude/
├── CLAUDE.md           # メイン指示（委任、テスト、ツール）
├── agents/             # 委任用の専門エージェント
│   ├── code-reviewer.md
│   ├── commit-drafter.md
│   ├── security-auditor.md
│   ├── test-auditor.md
│   ├── test-runner.md
│   └── ux-auditor.md
├── rules/              # コンテキスト対応ルール（ファイルタイプ別にロード）
│   ├── delegation.md
│   ├── planning.md
│   ├── security.md
│   ├── testing-core.md
│   ├── testing-python.md
│   └── testing-typescript.md
└── skills/             # ユーザー呼び出し可能なスラッシュコマンド
    ├── commit/
    └── handoff/
```

## インストール

`.claude/`ディレクトリをプロジェクトルートにコピー：

```bash
cp -r ja/.claude /path/to/your/project/
```

または、グローバルインストール（全プロジェクトに適用）：

```bash
cp -r ja/.claude ~/.claude
```

## 主要な原則

### 必須の委任
Opusは定型作業を専門エージェントに委任します：
- `commit-drafter` - gitコミット用（Haikuで実行）
- `test-runner` - テスト実行用（Haikuで実行）
- `code-reviewer` - コードレビュー用（Sonnetで実行）

### テスト原則
「失敗するテストは贈り物」テストは以下を満たすべき：
- 決定論的（sleep禁止、実際のネットワーク呼び出し禁止）
- 意味のある（型だけでなく、具体的な値をアサート）
- 境界でモック（内部ロジックではなく、HTTP/DB/ファイルシステムでモック）

### プロアクティブなスカウト
専門エージェントがプロアクティブに問題を検出：
- `security-auditor` - 認証コード、ファイルアップロード、APIエンドポイント後
- `ux-auditor` - UIコンポーネントとユーザーフロー後
- `test-auditor` - テスト品質レビュー時

## 言語

- [English](./en/) - 英語
- [日本語](./ja/) - 日本語（このバージョン）

## カスタマイズ

1. プロジェクトにコピー
2. `CLAUDE.md`をプロジェクト固有の指示に変更
3. `.claude/rules/`にプロジェクト固有のルールを追加
4. 不要なエージェントを削除

## 要件

- Claude Code CLI
- Git（commitスキル用）

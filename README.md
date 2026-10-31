# 🐙 Miskatonic Scribe

Bot do Telegram em Python (arquitetura modular **SOLID**) para registrar decks e cartas de **Arkham Horror: The Card Game (LCG)** a partir de mensagens de voz (`.ogg`), arquivos de áudio ou mensagens de texto, gerando arquivos `.o8d` (OCTGN) prontos para importação com 1 clique no **ArkhamDB**.

---

## ✨ Funcionalidades Principais

- **Três Modos de Transcrição e Extração (`TRANSCRIPTION_MODE`)**:
  - `whisper` *(padrão)*: Transcreve o áudio localmente via `openai-whisper` e envia o texto para o Gemini estruturar o investigador e as cartas.
  - `gemini_audio`: Envia o arquivo `.ogg` diretamente para o Gemini (multimodal), dispensando o processamento local do Whisper.
  - `hybrid`: Executa o Whisper localmente **e** envia o áudio `.ogg` junto com a transcrição ao Gemini para desambiguação fonética de nomes em PT-BR/EN.
- **Identificação de Investigador e Validação de Deckbuilding (`deck_options`)**:
  - Reconhece o investigador falado/escrito (ex.: *Diana Stanley*) e consulta automaticamente as regras oficiais do próprio `cards.json` do ArkhamDB (`deck_options`, facções permitidas, limites de XP, *traits* e cartas exclusivas).
  - Evita que cartas de facções ou níveis incompatíveis com o investigador (ou cartas de encontro/mito) sejam escolhidas por engano no *fuzzy matching*.
- **Edição Interativa por Texto Antes da Confirmação**:
  - Enquanto um lote de cartas aguarda confirmação, você pode enviar mensagens de texto (ex.: *"trocar Astral Travel por 2 Foi só um arranhão nível 2"* ou *"remover Lanterna"*) para ajustar o deck em tempo real.
- **Exportação Automática `.o8d` (OCTGN) para o ArkhamDB**:
  - Ao confirmar o lote (**✅ Confirmar**), o bot salva as cartas na sua coleção (`data/user_collections.json`) e envia um arquivo `.o8d` anexado no Telegram, pronto para importar em `https://arkhamdb.com/decks` (*Import Deck*).
- **Atualização Automática da Base do ArkhamDB**:
  - Baixa `cards.json` automaticamente de `https://arkhamdb.com/api/public/cards/` e renova o cache local periodicamente (padrão: a cada `30` dias via `ARKHAMDB_REFRESH_DAYS`) ou sob demanda pelo comando `/atualizar_cartas`.
- **Segurança por Whitelist**:
  - Apenas IDs numéricos do Telegram listados em `TELEGRAM_ALLOWED_USER_IDS` podem interagir com o bot.

---

## 🏗️ Arquitetura e Camadas (SOLID)

```text
miskatonic-scribe/
├── main.py                                    # Ponto de entrada e injeção de dependências
├── scripts/
│   └── sync_cards.py                          # Script CLI utilitário para sincronizar cards.json
├── src/miskatonic_scribe/
│   ├── config.py                              # Carregamento e validação de variáveis de ambiente (.env)
│   ├── logging_config.py                      # Configuração de logs em console e arquivo rotativo
│   ├── domain/
│   │   ├── exceptions.py                      # Exceções de domínio
│   │   ├── interfaces.py                      # Contratos/ABCs (Inversão de Dependência)
│   │   └── models.py                          # Entidades e DTOs (Pydantic + Dataclasses)
│   ├── infrastructure/
│   │   ├── arkhamdb_repository.py             # Cache local e download periódico da API do ArkhamDB
│   │   ├── arkhamdb_deck_exporter.py          # Gerador de arquivos .o8d (OCTGN XML)
│   │   ├── audio_storage.py                   # Armazenamento temporário de áudio e limpeza automática
│   │   ├── gemini_extractor.py                # Extração multimodal via Google GenAI SDK com fallback
│   │   ├── user_collection_repository.py      # Persistência JSON das coleções por usuário
│   │   └── whisper_transcriber.py             # Transcrição local de áudio com OpenAI Whisper
│   ├── services/
│   │   ├── card_matcher.py                    # Fuzzy matching (thefuzz) + validação de deck_options
│   │   └── registration_pipeline.py           # Orquestração fim-a-fim (áudio, texto e merge de lotes)
│   └── bot/
│       └── telegram_handlers.py               # Handlers python-telegram-bot v21+ e botões inline
└── tests/                                     # Suíte de testes automatizados (pytest)
```

---

## 🤖 Comandos do Bot no Telegram

| Comando | Descrição |
| :--- | :--- |
| `/start` ou `/help` | Exibe instruções de uso e exemplos. |
| `/colecao` ou `/collection` | Lista todas as cartas confirmadas e salvas na sua coleção. |
| `/atualizar_cartas` ou `/sync_cards` | Força o download imediato da base atualizada do ArkhamDB (`cards.json`). |
| `/logs` ou `/log` | Exibe as últimas linhas do arquivo de log do servidor diretamente no chat. |

---

## ⚙️ Configuração (`.env`)

Copie o arquivo de exemplo e preencha suas chaves:

```bash
cp .env.example .env
```

Principais variáveis em `.env`:

- `TELEGRAM_BOT_TOKEN`: Token do seu bot gerado no `@BotFather`.
- `TELEGRAM_ALLOWED_USER_IDS`: Lista separada por vírgula de IDs numéricos do Telegram autorizados.
- `GEMINI_API_KEY`: Chave da API do Google Gemini.
- `GEMINI_MODEL`: Modelo principal (padrão: `gemini-3.5-flash`).
- `GEMINI_FALLBACK_MODELS`: Cadeia de fallback em caso de erro `503`/`429` (padrão: `gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-latest`).
- `TRANSCRIPTION_MODE`: `whisper`, `gemini_audio` ou `hybrid`.
- `WHISPER_MODEL_NAME`: Modelo local do Whisper (`base`, `small`, etc.).
- `ARKHAMDB_REFRESH_DAYS`: Dias de validade do cache `cards.json` antes de atualizar automaticamente (padrão: `30`).

---

## 🚀 Execução Local e Deploy com Docker

### Execução Local

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

> **Nota**: Para os modos `whisper` e `hybrid`, o binário `ffmpeg` deve estar instalado e disponível no `PATH` do sistema.

### Deploy com Docker (Oracle Cloud Always Free / Linux)

```bash
docker compose up -d --build
docker compose logs -f
```

---

## 🌿 Fluxo de Trabalho Git (Branches, Testes e Limpeza)

Para manter o histórico limpo e estável, **todo desenvolvimento deve seguir obrigatoriamente este fluxo**:

1. **Criar uma branch dedicada para cada feature ou correção** a partir da `main`:
   - `feat/<nome-curto>` para novas funcionalidades (ex.: `git checkout -b feat/exportacao-o8d`)
   - `fix/<nome-curto>` para correções de bugs (ex.: `git checkout -b fix/deck-options-matcher`)
   - `docs/<nome-curto>` para melhorias de documentação
2. **Implementar e validar com a suíte de testes automatizados**:
   ```bash
   pytest -v
   ```
   Nenhum merge deve ser feito sem que 100% dos testes passem.
3. **Fazer o merge na `main` após validação**:
   ```bash
   git checkout main
   git pull origin main
   git merge --no-ff <nome-da-branch>
   git push origin main
   ```
4. **Limpar branches antigas / já mescladas (local e remoto)**:
   ```bash
   # Remover a branch local já mesclada
   git branch -d <nome-da-branch>

   # Remover a branch remota correspondente (se tiver sido enviada ao origin)
   git push origin --delete <nome-da-branch>

   # Limpar referências remotas obsoletas
   git fetch --prune
   ```

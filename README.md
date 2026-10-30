# 🐙 Miskatonic Scribe

Bot do Telegram em Python (arquitetura modular SOLID) focado em registrar cartas de **Arkham Horror: The Card Game (LCG)** a partir de mensagens de voz (`.ogg`) ou arquivos de áudio.

---

## 🏗️ Arquitetura e Pipeline

O projeto segue os princípios **SOLID** com inversão de dependência e separação clara em camadas (`domain`, `infrastructure`, `services`, `bot`):

1. **Listener (`MiskatonicTelegramBot` + `LocalAudioStorageManager`)**:
   - Utiliza `python-telegram-bot` (v21+).
   - Escuta mensagens de voz (`voice`) ou áudio (`audio`), realiza o download do arquivo `.ogg` localmente e executa automaticamente a rotina de manutenção de armazenamento (limpa o diretório a cada **5 arquivos armazenados** ou arquivos com mais de **X dias**).
2. **Transcrição Local (`WhisperAudioTranscriber`)**:
   - Utiliza `openai-whisper` rodando localmente com o modelo `base` (carregado sob demanda com cache em memória) e prompt contextualizado para reconhecer jargões misturados em PT-BR e EN.
3. **Estruturação via LLM com Fallback (`GeminiCardIntentExtractor`)**:
   - Utiliza o SDK `google-genai` com o prompt de sistema estrito para tradução ao nome oficial em Inglês e extração de XP e quantidade.
   - Suporta cadeia de **fallback de modelos** (`GEMINI_MODEL` → `GEMINI_FALLBACK_MODELS`) com retentativas e *exponential backoff* (`GEMINI_MAX_RETRIES`, `GEMINI_INITIAL_DELAY`) em caso de *rate-limit* (`429`), alta demanda (`503`) ou falhas de validação JSON.
4. **Fuzzy Matching no ArkhamDB (`ArkhamDBCardRepository` + `ArkhamFuzzyCardMatcher`)**:
   - Caso `cards.json` não exista localmente, realiza o download automático de `https://arkhamdb.com/api/public/cards/`.
   - Utiliza `thefuzz` para cruzar os nomes em Inglês e nível (`xp`) extraídos pelo Gemini com `real_name` / `name` do ArkhamDB.
   - Prioriza o lançamento original (menor `code`, ignorando reimpressões `duplicate_of_code`) e lista variações legítimas (como subtítulos distintos, ex.: *Archaic Glyphs: Guiding Stones* vs *Prophecy Foretold*).
5. **Resposta e Confirmação (`InlineKeyboardMarkup` + `JsonUserCollectionRepository`)**:
   - Envia a lista formatada (ex.: `2x Shrivelling (Nível 3) - ID: 02066`) com botões inline **✅ Confirmar** e **❌ Cancelar**, persistindo as cartas confirmadas por usuário em `data/user_collections.json`.

---

## 🚀 Instalação e Configuração

```bash
# 1. Criar ambiente virtual e instalar dependências
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Configurar variáveis de ambiente
copy .env.example .env
```

Edite o arquivo `.env` com suas chaves `TELEGRAM_BOT_TOKEN` e `GEMINI_API_KEY`.

> **Nota sobre o Whisper**: O `openai-whisper` requer o binário `ffmpeg` instalado e disponível no `PATH` do sistema operacional para decodificar arquivos `.ogg`.

---

## ▶️ Execução

```bash
python main.py
```

## 🧪 Testes Automatizados

```bash
pytest -v
```

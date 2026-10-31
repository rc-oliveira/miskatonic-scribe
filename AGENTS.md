# Diretrizes do Repositório (AGENTS.md)

Este arquivo define regras obrigatórias de engenharia e fluxo de trabalho Git para qualquer agente de IA ou desenvolvedor atuando neste repositório (`miskatonic-scribe`).

## 1. Fluxo Obrigatório de Branches e Limpeza (Git Workflow)

- **Nunca commite diretamente na `main`** para desenvolver features ou correções.
- **Sempre crie uma branch específica** antes de iniciar alterações:
  - `feat/<descricao>` para novas funcionalidades.
  - `fix/<descricao>` para correções de bugs.
  - `docs/<descricao>` para atualizações de documentação.
- **Validação com Testes**:
  - Execute `pytest -v` e adicione testes cobrindo o comportamento novo ou corrigido antes de concluir.
- **Merge e Limpeza de Branches em Desuso**:
  - Após testar e validar a branch, faça o merge na `main` e **remova imediatamente a branch mesclada** (tanto localmente com `git branch -d <branch>` quanto no remoto com `git push origin --delete <branch>`, além de `git fetch --prune`), mantendo apenas a `main` e eventuais branches com trabalho em andamento.

## 2. Princípios de Arquitetura e Manutenibilidade

- **Regras Data-Driven (Sem Hardcoding de Cartas/Investigadores)**:
  - Toda validação de investigadores e cartas (como `deck_options`, facções e níveis) deve ser lida dinamicamente do `cards.json` oficial do ArkhamDB e operar em modo *fail-open* (permissivo para chaves futuras desconhecidas), evitando manutenção manual quando novas expansões forem lançadas.
- **Separação de Camadas (SOLID)**:
  - Mantenha a separação estrita entre `domain`, `infrastructure`, `services` e `bot`.

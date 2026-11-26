---
name: warroom
description: Sala de guerra - pega as varias atividades que estao esperando ou que vao ser executadas e divide entre varios agentes (agy, Haiku, Ollama, OmniRoute) trabalhando AO MESMO TEMPO, cada um numa area de arquivos propria para nao atrapalhar os outros; o maestro lanca, confere a area de cada um, junta uma por vez com a verificacao rodando no projeto real e faz o commit. Use quando o usuario digitar /warroom, falar em "war room", "sala de guerra", "varias frentes", "em paralelo", "varios agentes ao mesmo tempo", "abrir varias instancias do agy", ou quando houver 3 ou mais tarefas independentes na fila.
---

# /warroom: varias frentes ao mesmo tempo, sem uma pisar na outra

Voce e o comandante. Os executores baratos fazem as frentes em paralelo; voce **divide, especifica, confere e junta**. Enquanto eles rodam, voce trabalha no que so voce faz (design, especificar a proxima frente, revisar a que chegou). Tudo roda pelo `python ~/.maestro/maestro.py sala ...` (abaixo, `sala`). Siga tambem a skill `maestro` para escolher executor e escrever especificacao.

**Se a variavel de ambiente `MAESTRO_EU` existe, voce e um executor: ignore esta skill.**

## 1. Levante a fila

Junte as atividades que estao esperando ou vao ser executadas, nesta ordem de fonte:
1. o que o usuario listou na mensagem do `/warroom` (as palavras dele entram nas tarefas sem parafrasear);
2. o roteiro/TODO/issues do projeto (`ROTEIRO.md`, `TODO.md`, `docs/`, `gh issue list`), e o que ficou pendente nesta conversa;
3. jobs abertos do maestro (`maestro.py jobs`) e salas que ja existem (`sala status <nome>`).

Se a fila tiver menos de 3 atividades independentes, nao abra sala: use o `maestro run` normal.

## 2. Divida em frentes que nao se pisam

Para cada atividade, decida a **area**: os arquivos (globs relativos a raiz do repo) que ela cria ou altera. A regra que fez a sala do jogo funcionar com 4 executores ao mesmo tempo:

- **Frente boa cria arquivos novos numa pasta so dela** (`scripts/run/**`, `testes/run/**`). Sistemas novos nascem soltos, com teste proprio, e ainda NAO ligados no resto.
- **Integracao e uma frente separada**, depois (`depois = [...]`), porque mexe em arquivos compartilhados (jogador, HUD, main, config). Duas integracoes no mesmo arquivo nunca rodam juntas: o maestro serializa sozinho quem tem area cruzada, na ordem do plano.
- **Area vazia = a frente roda sozinha.** Sempre declare a area.
- **Arquivos que ninguem pode alterar** (testes existentes, a infraestrutura de teste, config de CI) vao em `protegidos`: criar arquivo novo ali pode, alterar/apagar o que existe nao. Isso impede o executor de enfraquecer teste para passar.
- **Infra compartilhada (runner de teste, build, tipos base) quem mexe e voce**, fora da sala, com commit, ANTES de lancar quem depende dela. Na sala do jogo uma frente travou porque o runner de teste estourava o tempo: a correcao era de todos, nao da frente.
- Frente grande demais (mais de ~800 linhas ou mais de uma ideia) vira duas, a segunda com `depois`.
- `paralelo` 3 a 4 costuma ser o certo (cotas do agy e a CPU da verificacao). So uma frente por vez usa o modelo local (GPU); as outras seguem a rota sem ele, automaticamente.

## 3. Escreva uma tarefa por frente

Um `.md` por frente no scratchpad, no padrao da skill `maestro` (contexto com o que ja existe e as assinaturas que a frente vai usar, o que fazer, como testar). Nao precisa repetir a regra da area: o maestro anexa no fim de cada tarefa um bloco "Regras da sala de guerra" com a area e os protegidos, e manda o executor responder `ESCALAR:` se precisar sair dela.

## 4. Escreva o plano (`sala.toml`, no scratchpad)

```toml
nome = "jogo-marco1"            # opcional; padrao = nome do arquivo
dir = "C:/caminho/do/repo"      # relativo ao plano ou absoluto
verificar = "bash tools/verificar.sh"   # roda no worktree de cada frente E no projeto real a cada juntada
paralelo = 4
juntar = "revisar"              # revisar: voce confere cada frente antes de juntar | auto: junta sozinho se a verificacao passar
commitar = true                 # cada juntada vira um commit (facil de reverter)
protegidos = ["testes/nucleo/**", "tools/**"]
eu = "4"                        # seu nivel (skill maestro)
rodape = """Co-Authored-By: ..."""   # linhas de atribuicao que as suas instrucoes pedem nos commits

[[frente]]
id = "B"
titulo = "feat: gerador da run do Ibura"   # vira a 1a linha do commit
tarefa = "tarefa_run.md"
area = ["scripts/run/**", "testes/run/**"]
tipo = "implementar"            # rota do maestro; exec = "agy-hydrogen-high" para forcar
# depois = ["A"]  timeout = 1800  tentativas = 2  verificar = "..." (sobrepoe o da sala)
```

Rode `sala checar sala.toml`: confere o plano (ids, dependencias, ciclos, tarefas, areas) e mostra as **ondas** (quem parte junto, quem espera quem). Ajuste ate as ondas ficarem como voce quer.

## 5. Abra a sala e trabalhe enquanto ela roda

Lance o vigia **em background** (no Claude Code: Bash com `run_in_background: true`):

```
python ~/.maestro/maestro.py sala rodar <plano.toml>
```

Ele lanca as frentes livres (processos soltos, que sobrevivem se o vigia fechar), olha a cada 10 s e **termina no primeiro evento que precisa de voce**, imprimindo o evento, o quadro e os proximos comandos. A notificacao de fim do background e o seu aviso; nao fique consultando. Enquanto isso, faca o seu trabalho de comandante (proxima especificacao, design, revisar o que ja chegou).

Mostre ao usuario o quadro da sala como tabela curta (frente, executor, estado, o que faz) quando lancar e a cada rodada de juntadas.

## 6. A cada evento

| Estado | O que fazer |
|---|---|
| `pronta` | Confira: `maestro.py diff <job> --stat`, depois os arquivos que importam (`--arquivo`). Testes existentes nao podem ter sido enfraquecidos. OK -> `sala juntar <sala> <id>` |
| `invadiu` | Mexeu fora da area ou em protegido (a obs lista). Se foi lixo: `sala retomar <sala> <id> "desfaca X, a area e so Y"`. Se a mudanca era necessaria e voce conferiu que nao briga com outra frente: `sala juntar <sala> <id> --forcar` |
| `falhou` | Leia o resumo do evento. Erro de especificacao -> `sala retomar` com a correcao. Ambiente/infra -> corrija voce, commite e `sala refazer` |
| `duvida` | O executor escalou. Decida e `sala retomar <sala> <id> "sua decisao"` |
| `conflito` | A juntada foi desfeita: o patch nao entrou ou a verificacao falhou com ela junto das outras (conflito de sentido entre frentes que passaram sozinhas). `sala refazer <sala> <id>` roda de novo a partir do projeto atual; ou `sala retomar` explicando o que mudou |

`sala juntar` aplica no projeto real, roda o `verificar` ali, faz o commit (os hooks do repo rodam) e apaga o worktree. Se qualquer passo falhar, **desfaz** e o projeto fica como estava. Ele recusa juntar se os arquivos da frente tem mudanca nao commitada no projeto: commite o seu trabalho antes.

Depois de tratar os eventos, **rode `sala rodar <sala>` de novo em background**: ele mostra eventos que chegaram nesse meio-tempo e lanca as frentes que ficaram livres (as que dependiam da que voce juntou).

## 7. Plano vivo

A sala aceita frentes novas no meio da guerra: acrescente `[[frente]]` no mesmo `.toml` e rode `sala rodar` de novo. Frente que ja partiu nao muda; frente ainda em `espera` pega a versao nova do plano; frente tirada do plano antes de partir sai da fila.

Outros comandos: `sala status <sala>` (quadro), `sala parar <sala> [id]` (mata a frente), `sala descartar <sala> <id>`.

## 8. Fechar

Quando o quadro mostrar tudo `juntada`/`descartada`: rode a verificacao inteira uma vez no projeto, mostre ao usuario o quadro final (commits por frente, testes passando, o que ficou para depois) e diga o que ele pode testar agora.

## Erros que ja custaram caro

- Lancar frentes com `&` + `sleep` no shell, sem vigia: o orquestrador perdia o fim de um job. Use `sala rodar` em background.
- Duas frentes "so arquivos novos" que, juntas, quebravam: a verificacao no projeto real a cada juntada existe para isso; nao desligue o `verificar`.
- Corrigir a infra numa frente e copiar o arquivo a mao para os outros worktrees: corrija no projeto, commite e use `sala refazer` nas frentes que dependiam.

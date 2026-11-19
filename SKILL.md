---
name: maestro
description: Divide a tarefa entre modelos de varias empresas para gastar o minimo de token. Vale para qualquer modelo, do mais fraco ao mais forte - cada parte vai para o menor modelo que da conta. O que exige mais que voce sobe como consulta curta a um modelo maior; o trabalho bracal desce para um modelo igual ou menor (1) Ollama local (gratis)  2) OmniRoute (gratis)  3) agy (Gemini Flash)  4) Claude Code (Haiku)) via `maestro.py`, em worktree isolado. Use no inicio de qualquer tarefa com varios passos (implementacao, refatoracao, testes, investigacao, leitura de logs ou codigo grande) e sempre que o usuario falar em economizar token, delegar, usar modelo barato ou maior, agy, flash, haiku ou dividir o trabalho.
---

# Maestro: cada parte no menor modelo que da conta

Voce orquestra, seja qual for o seu tamanho. Para cada parte da tarefa a pergunta e a mesma: **qual e o menor modelo que resolve isto?** Se for maior que voce, suba so a duvida. Se for igual ou menor, desca o trabalho. O usuario autoriza delegar e consultar sem pedir confirmacao.

Ferramenta: `python ~/.maestro/maestro.py` (abaixo, `maestro`), rodada pelo seu terminal, seja qual for o CLI (Claude Code, agy, opencode, Gemini CLI, Codex). Se o shell nao expande `~`, use o caminho completo da pasta do usuario (no Windows, `%USERPROFILE%\.maestro\maestro.py` ou `$HOME\.maestro\maestro.py`).

**Se a variavel de ambiente `MAESTRO_EU` existe, voce e um executor lancado pelo maestro: ignore esta skill**, faca a tarefa e, se faltar uma decisao, responda `ESCALAR: <pergunta>`. O maestro recusa delegar de dentro de um executor.

**Guarda (Claude Code):** hooks instalados pelo `instalar.py` lembram este criterio a cada tarefa nova, avisam depois de ~12 chamadas de execucao sem delegar e, se a sessao trabalhou muito sem delegar nada, seguram o fim uma vez pedindo justificativa. Quando aparecer `[maestro]` no contexto, responda com a decisao declarada (`maestro: sim - ...` ou `maestro: nao - motivo`) e aja de acordo; nao ignore. `python ~/.maestro/guarda.py relatorio` mostra quanto cada sessao delegou.

## 0. Saiba o seu nivel

| Nivel | Aguenta | Modelos |
|---|---|---|
| 4 | arquitetura, causa raiz, seguranca, concorrencia, decisao sem volta | Fable, Opus, Gemini 4 Carbon, Gemini 4 Neon xhigh, GPT de topo |
| 3 | implementacao delicada, depuracao com muito contexto, revisar diff | Sonnet, Argon, Gemini 4 Neon medium/high, Gemini 4 Hydrogen high/xhigh, Gemini Pro, Kimi/GLM/DeepSeek grandes |
| 2 | implementar com especificacao fechada, testes, docs | Haiku, Gemini Flash high/medium, Gemini 4 Hydrogen medium/low, ollama-qwen-code, ollama-gpt-oss, free-code, modelos "mini" |
| 1 | ler, coletar, extrair fatos, edicao mecanica | Flash low/lite, ollama-gemma4, free-fast, modelos "nano" |

Passe o seu nivel em `--eu` (o numero, ou o seu id do catalogo, ex.: `--eu cc-haiku`). Se o seu modelo nao esta na tabela, compare pelo nome (pro/sonnet = 3, flash/haiku/mini = 2) e, na duvida, use 2. Antes da primeira chamada da sessao rode `maestro rota --eu <voce>`: mostra, sem gastar nada, quem esta abaixo ou igual a voce, quem esta acima e o que esta esgotado.

## 1. Triagem (antes da primeira ferramenta)

Divida a tarefa em partes, de a cada uma o nivel que ela exige (tabela acima) e escreva uma linha de plano, por exemplo: `Eu = 2. Coleta (1): desce. Implementacao (2): desce. Escolha do esquema do banco (4): consulto.`

| A parte exige | O que fazer |
|---|---|
| mais que o seu nivel | **subir**: `maestro consultar` com uma pergunta fechada; voce continua com a resposta |
| o seu nivel ou menos, e e pequena (< ~20 linhas, arquivo que voce ja leu) | voce mesmo: especificar custa mais |
| o seu nivel ou menos, e e grande | **descer**: `maestro run --eu <voce>` (coleta com `--leitura`) |

Regras de economia:

- Nao suba o que um teste responde. Se rodar um comando decide a questao, rode.
- Nao suba a tarefa inteira, so a decisao. O modelo maior responde em ate 25 linhas e quem digita continua sendo o menor.
- Uma consulta por decisao. Junte as duvidas da mesma parte numa pergunta so.
- Teste de cada parte que desce: *se vier errado, eu percebo na conferencia?* Se nao, nao desca.
- Nivel 4 nao tem para onde subir: decide ou pergunta ao usuario.

**Requisicoes que so queimam token (nao faca):**

- delegar o que cabe em ~20 linhas de um arquivo que voce ja leu;
- pedir de novo o que ja esta no seu contexto, ou reler arquivo que voce ja leu;
- mandar "explore o projeto" ou "veja o que acha": toda leitura tem pergunta fechada e formato de resposta;
- subir uma duvida sem as opcoes que voce ve e sem o que ja verificou;
- abrir job novo para corrigir um job: use `--continuar` (mesma sessao, o executor nao rele tudo);
- pedir o log, o diff ou a saida inteira antes de olhar o resumo.

**Onde NAO economizar (e aqui que se fica burro):**

- o pedido do usuario entra na especificacao com as palavras dele, sem parafrasear requisito;
- mensagens de erro, caminhos, numeros e nomes vao literais, nunca resumidos;
- a conferencia do diff e a verificacao nao se pulam;
- ambiguidade real vira pergunta ao usuario, nao chute;
- resumo cortado sempre diz onde esta o completo: se o resumo nao explica a falha, abra o completo em vez de adivinhar.

O maestro avisa (`AVISO:`) quando a tarefa sai sem arquivo citado, sem `--verificar`, pequena demais ou com pergunta vaga. Aviso nao bloqueia: corrija a tarefa ou siga se tiver motivo.

## 2. Subir: consultar um modelo maior

```
maestro consultar --eu <voce> --dir <projeto> "pergunta"      (ou --tarefa duvida.md)
```

Escolhe o modelo mais barato **acima** de voce (primeiro o nivel seguinte, e dentro do nivel a ordem de gasto), em modo so leitura. Devolve DECISAO, MOTIVO, PASSOS, RISCOS.

A pergunta precisa se bastar, porque o modelo maior nao viu a sua conversa: o que voce quer decidir, as opcoes que ve, o que ja verificou (com caminho:linha e saida literal) e os arquivos que ele deve olhar. Pergunta vaga faz o modelo caro ler o projeto inteiro.

- `--min 4`: exige o topo (arquitetura, seguranca). Sem isso, um nivel acima ja responde.
- `--sensivel`: pula os externos (OmniRoute).
- Avise o usuario em uma linha quando consultar (gasta um modelo com flag `poupar`).

## 3. Descer: escolher o executor

**Ordem de gasto definida pelo usuario, nesta ordem exata: 1) Ollama local (gratis)  2) OmniRoute (gratis)  3) agy (Gemini Flash)  4) Claude Code (Haiku).** So passe para o proximo bolso quando o anterior estiver sem cota.

`--exec auto` (padrao) usa a rota do `--tipo` no catalogo, tira quem e maior que o `--eu`, e passa ao proximo sozinho quando a cota acaba.

| Parte | Como chamar |
|---|---|
| Coletar / ler | `maestro run --leitura --eu <voce> --tipo ler "pergunta"` (dentro do Claude Code, os subagentes nativos `Explore`/`mecanico` gastam Claude: so quando os outros bolsos estiverem esgotados ou para 1 a 2 chamadas) |
| Implementar, testes, refatorar | `maestro run --eu <voce> --tipo implementar ...` |
| Tarefa pequena, docs | `--tipo pequena` / `--tipo docs` |
| Codigo com segredo ou de cliente | acrescente `--sensivel` (pula so o OmniRoute, mantendo `local`, `agy` e `claude`) |
| Execucao que exige mais que voce | `--tipo dificil` (credit-strong > Hydrogen high > Neon high > Argon > Opus no agy > Sonnet); avise o usuario em uma linha |

- `--exec a,b,c` forca uma lista propria; use so com motivo (ex.: `maestro sugerir <tipo>` mostra que um executor erra muito nesse tipo).
- Nunca coloque segredos (chaves, senhas, tokens) em nenhuma tarefa nem consulta.

## 4. Especificar (o que faz o executor acertar de primeira)

Grave a tarefa num arquivo (ex.: na pasta de rascunho) e passe com `--tarefa`. Modelo:

```markdown
# Tarefa: <nome curto>
## Contexto
- Arquivos e funcoes exatos ("em `get_meta` de `meta.py`"); o que ja existe e deve ser reaproveitado.
- Pegadinhas do ambiente.
- PROIBIDO: mexer em <X, Y>; imprimir segredos; instalar pacotes; comandos > 300 s.
## O que fazer
1. Comportamento exato, com exemplo de entrada e saida e numeros concretos.
## Fora de escopo
Tudo o resto.
## Verificacao
Comando(s) e resultado esperado.
```

- Uma tarefa = uma entrega. Grande vira A, B, C.
- Prefira alvo executavel: peca primeiro os testes (tarefa A), depois a implementacao ate passarem (B).
- Sempre passe `--verificar "<comando>"`: o maestro roda no worktree e devolve OK/FALHOU por exit code. Isso substitui o "aprovado" do executor.

## 5. Rodar

```
maestro run --eu <voce> --dir <projeto> --tipo implementar --tarefa <arq.md> --verificar "<cmd>"
maestro run --leitura --eu <voce> --dir <projeto> "pergunta de coleta"
```

- O projeto precisa ser git com commit inicial (o executor trabalha num worktree em `~/.maestro/wt/`, com autonomia total, sem tocar o projeto real). Mudancas nao commitadas e arquivos nao rastreados do usuario sao copiados para o worktree.
- Tarefas que podem passar de 1 minuto: rode em segundo plano e trabalhe em outra parte enquanto isso. Jobs paralelos sao seguros (cada um tem seu worktree), mas evite dois jobs no mesmo arquivo: a juntada do segundo pode conflitar.
- Leia so o resumo impresso. O log completo fica em `~/.maestro/jobs/<job>/`, abra apenas para investigar falha.

**Quando o modelo local nao cabe na memoria**, o maestro resolve sozinho, sem gastar token seu: abre uma janela no PC e o usuario marca os apps que pode fechar. Sim: ele fecha e usa o local. Nao, ou sem resposta em 2 min: segue a rota sem o local (linha `pulando ...` no resumo). Nao faca nada com isso.

So se a janela nao abrir (ou com `--sem-janela`) o run para com saida 5 (`PC SEM FOLGA PARA O MODELO LOCAL`), listando os apps. **Nunca feche nada sem o usuario autorizar** (isso vale mesmo com a delegacao automatica liberada):

1. Pergunte ao usuario (AskUserQuestion), citando o modelo, quanto falta e os apps marcados com `*`, com as opcoes: fechar os sugeridos / escolher quais fechar / nao fechar e usar o proximo executor.
2. Autorizou: `maestro liberar --apps <so os que ele aprovou>` e repita o mesmo `run`. App que nao fechou sozinho (pode estar pedindo para salvar) so vai com `--forcar` se o usuario aceitar perder o que nao foi salvo.
3. Nao autorizou, ou o relatorio diz que nem fechando tudo cabe: repita o `run` com `--pular-local` (segue a rota sem os locais que nao cabem).

`maestro recursos [--exec ID]` mostra a mesma conta sem rodar nada. Executor lancado pelo maestro nao pode rodar `liberar`.

**Quando o executor trava numa decisao**, ele responde `ESCALAR: <pergunta>` em vez de chutar:

- Voce e maior que ele: a pergunta volta para voce (status `DUVIDA`, saida 4). Responda com `maestro run --continuar <job> "decisao"`. Se a pergunta passa do seu nivel, `maestro consultar` antes.
- Voce e do mesmo nivel ou menor: o maestro consulta um modelo acima e o mesmo executor continua, na mesma sessao (linha `escalou: a -> b` no resumo). Limite: `--escalar N`, padrao 1; `--escalar 0` desliga.

## 6. Conferir (nao pule)

1. `maestro diff <job> --stat` e depois `maestro diff <job>`: mexeu so onde devia? O diff para em 400 linhas e avisa; o resto se le por arquivo (`--arquivo <caminho>`) ou inteiro (`--max 0`). Diff cortado nao foi conferido.
2. Verificacao OK? Quando falha, o resumo traz so as linhas de falha, o inicio e o fim; a saida inteira fica no caminho indicado. Se a tarefa importa, rode tambem seus proprios comandos dentro do worktree (caminho no resumo).
3. Procure segredos no diff.
4. Aprovado: `maestro juntar <job>` (aplica no projeto real, sem commit). Rejeitado: `maestro descartar <job>`.

Falha de verificacao o maestro ja corrige sozinho: devolve a saida ao executor e roda de novo (`--tentativas`, padrao 2; `--orcamento N` limita os tokens do executor), e para quando passa, quando repete a mesma falha ou quando o job chega a 3 rodadas. Voce so le o resumo final (linha `correcao automatica:`). Se ele diz `correcao automatica parou`, nao mande "tente de novo": `maestro run --continuar <job> "<o que mudar, com saida literal>"` com uma instrucao nova (mesma sessao e worktree). Maximo 3 rodadas. Depois: nivel 3 ou 4 corrige direto no worktree e junta; nivel 1 ou 2 consulta acima com o diff e o erro, ou descarta.

Se a parte exige um nivel acima do seu para **conferir** (ex.: voce e 2 e o diff mexe em concorrencia), suba a revisao: `maestro consultar --eu <voce> --dir <worktree> "revise o diff contra a tarefa: ..."`.

## 7. Entregar

Um resumo final ao usuario: o que foi feito, por quem (executor), o que foi consultado acima e a quem, o resultado da verificacao e o que ficou pendente. Commit so se o usuario pedir.

Ao fim de uma tarefa longa, `maestro ganho --dias 1`: mostra quanto os executores processaram, quanto voltou para voce e onde se perdeu (job descartado, 3 rodadas, delegacao pequena demais, pedido repetido). Se apontar perda, diga ao usuario em uma linha e ajuste a proxima triagem.

Mudanca nesta skill, no catalogo ou nas rotas so fica se `maestro ganho --dias 7 --comparar` mostrar melhora (1a rodada ok, rodadas por job, juntado, tokens do orquestrador por job). Junte as mudancas e aplique de vez em quando: reescrever a skill a cada sessao invalida o cache do prompt.

Contexto longo: em vez de compactar no meio, feche a fase (tarefa juntada e verificada), registre o que falta e comece a proxima fase com o contexto limpo. Compactar no meio do trabalho custa tokens e perde detalhe.

## Comandos

| Comando | Para que |
|---|---|
| `maestro rota --eu <voce> [--tipo t]` | quem esta abaixo/igual e acima de voce; nao gasta nada |
| `maestro consultar --eu <voce> "duvida"` | subir uma decisao |
| `maestro catalogo [--completo]` | executores, nivel, bolso, custo, esgotados |
| `maestro jobs` | jobs com worktree aberto |
| `maestro cota <id> --horas 120` / `--liberar` | marcar/liberar cota manualmente |
| `maestro sugerir [tipo]` | taxa de acerto por executor |
| `maestro ganho [--dias 30] [--comparar]` | tokens processados pelos executores, o que voltou, onde se perdeu; `--comparar` poe o periodo lado a lado com o anterior |

Adicionar executor: edite `~/.maestro/catalogo.toml` (cli `claude`, `ollama`, `agy` ou `opencode`; `nivel` de 1 a 4).

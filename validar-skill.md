---
name: claudevalidate{modelo}
description: Revisao de senior pelo Claude {Nome} ({esforco}, so leitura) do trabalho feito nesta sessao, por qualquer modelo (agy, Haiku, Hydrogen...). Devolve VEREDITO APROVADO / APROVADO COM RESSALVAS / REPROVADO com os problemas em caminho:linha, a origem de cada um (especificacao ou executor) e licoes para as proximas especificacoes. Use so quando o usuario pedir /claudevalidate{modelo}.
disable-model-invocation: true
---

# Validar com o {Nome}

Um revisor de topo ({Nome}, {esforco}, so leitura) confere se o trabalho desta sessao pode ser entregue, como um engenheiro senior com 50 anos de experiencia. Voce nao julga o proprio trabalho: prepara o pedido, roda o comando e repassa o veredito.

O revisor segue uma regua fixa: so reprova por problema com cenario concreto, aponta a classe inteira do defeito (todas as ocorrencias, a invariante e o teste que a prova) e diz se a culpa foi da especificacao ou do executor. Problema antigo, teste so possivel ao vivo e estilo ficam fora do veredito. APROVADO e o resultado normal de trabalho correto.

1. Grave num arquivo temporario (pasta de rascunho ou temp do sistema):
   - o que o usuario pediu, **com as palavras dele**: copie os pedidos da conversa, inclusive correcoes que ele fez no caminho;
   - **a especificacao que guiou o trabalho**: se voce delegou (maestro, subagente, outro modelo), cole a tarefa que mandou (o `.md` de `--tarefa`); se voce mesmo fez, escreva as decisoes de projeto que tomou e as que deixou para depois. O revisor usa isso para separar erro de especificacao de erro de execucao;
   - em 3 a 8 linhas, o que foi feito e o que ficou pendente ou incerto, inclusive o que so da para testar ao vivo (aparelho, conta, rede real). Nao defenda o trabalho. Pendencia declarada aqui nao conta como problema;
   - se e uma revalidacao, quais itens da validacao anterior o usuario **decidiu nao corrigir** (com as palavras dele), para nao voltarem como problema;
   - se o usuario escreveu algo junto do comando ($ARGUMENTS), isso entra como foco da revisao.
2. Escolha a fonte do trabalho:
   - mudancas ainda nao commitadas: padrao, sem opcao;
   - trabalho ja commitado nesta sessao: `--base <commit de antes dele>` (veja `git log --oneline`);
   - job do maestro ainda nao juntado: `--job <job>` (a especificacao do job ja vai junto).
3. Rode e espere (leva alguns minutos; nao interrompa nem rode em paralelo outra validacao):
   `python ~/.maestro/maestro.py validar --modelo {modelo} --dir <projeto> --tarefa <arquivo> [--base REF | --job JOB] [--verificar "<comando de teste do projeto>"]`
   Se o shell nao expande `~`: `%USERPROFILE%\.maestro\maestro.py` (Windows) ou o caminho completo.
   Revalidacao: a validacao anterior do mesmo projeto (ate 12 h) vai junto sozinha, com o diff do que mudou desde ela, e o revisor confere primeiro se cada item dela foi corrigido. Se for outro trabalho no mesmo projeto, passe `--anterior nenhuma`.
   Diff grande (o maestro avisa acima de 4000 linhas) ou mudanca critica (dados, dinheiro, seguranca, algo que nao tem volta): acrescente `--esforco high` (no /claudevalidateopus) ou use o /claudevalidateopusmax; em esforco medio o revisor le por amostragem.
4. Mostre ao usuario o VEREDITO, o RESUMO, o ANTERIOR (se veio) e os PROBLEMAS como vieram, sem suavizar nem contestar. FORA DO VEREDITO em uma linha cada. Se aparecer `VEREDITO PELA REGUA`, ele vale sobre o que o revisor escreveu. Saida 0 = aprovado (com ou sem ressalvas), 3 = reprovado, 1 ou 2 = erro ou timeout: diga que **nao foi validado**.
5. Se veio REPROVADO ou com ressalvas, liste o que corrigir e pergunte se deve corrigir. Ao corrigir:
   - corrija a **classe** do problema, nao so a linha citada: garanta a invariante em todos os caminhos que o revisor listou e procure outros do mesmo tipo;
   - escreva o teste que o revisor indicou antes de dar por corrigido;
   - se delegar a correcao, a tarefa leva a invariante, todas as ocorrencias e o teste, nao so "corrija o item 2";
   - nao mexa em FORA DO VEREDITO sem o usuario pedir.
   As LICOES (problemas de origem [spec]) ficam guardadas por projeto e entram sozinhas nas proximas tarefas do maestro; `maestro.py licoes --dir <projeto>` mostra quais. Depois de corrigir, a nova validacao e outro /claudevalidate{modelo}.

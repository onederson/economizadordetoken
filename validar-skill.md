---
name: claudevalidate{modelo}
description: Revisao de senior pelo Claude {Nome} (esforco medio, so leitura) do trabalho feito nesta sessao, por qualquer modelo (agy, Haiku, Argon...). Devolve VEREDITO APROVADO / APROVADO COM RESSALVAS / REPROVADO com os problemas em caminho:linha. Use so quando o usuario pedir /claudevalidate{modelo}.
disable-model-invocation: true
---

# Validar com o {Nome}

Um revisor de topo ({Nome}, esforco medio, so leitura) confere se o trabalho desta sessao esta certo, como um engenheiro senior com 50 anos de experiencia. Voce nao julga o proprio trabalho: prepara o pedido, roda o comando e repassa o veredito.

1. Grave num arquivo temporario (pasta de rascunho ou temp do sistema):
   - o que o usuario pediu, **com as palavras dele**: copie os pedidos da conversa, inclusive correcoes que ele fez no caminho;
   - em 3 a 8 linhas, o que foi feito e o que voce sabe que ficou pendente ou incerto. Nao defenda o trabalho;
   - se o usuario escreveu algo junto do comando ($ARGUMENTS), isso entra como foco da revisao.
2. Escolha a fonte do trabalho:
   - mudancas ainda nao commitadas: padrao, sem opcao;
   - trabalho ja commitado nesta sessao: `--base <commit de antes dele>` (veja `git log --oneline`);
   - job do maestro ainda nao juntado: `--job <job>`.
3. Rode e espere (leva alguns minutos; nao interrompa nem rode em paralelo outra validacao):
   `python ~/.maestro/maestro.py validar --modelo {modelo} --dir <projeto> --tarefa <arquivo> [--base REF | --job JOB] [--verificar "<comando de teste do projeto>"]`
   Se o shell nao expande `~`: `%USERPROFILE%\.maestro\maestro.py` (Windows) ou o caminho completo.
4. Mostre ao usuario o VEREDITO, o RESUMO e os PROBLEMAS como vieram, sem suavizar nem contestar. Saida 0 = aprovado (com ou sem ressalvas), 3 = reprovado, 1 ou 2 = erro ou timeout: diga que **nao foi validado**.
5. Se veio REPROVADO ou com ressalvas, liste o que corrigir e pergunte se deve corrigir. Depois de corrigir, a nova validacao e outro /claudevalidate{modelo}.

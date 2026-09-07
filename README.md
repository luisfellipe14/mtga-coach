# MTGA Coach

App local para rever as próprias decisões no MTG Arena e ajustar decks a partir dos logs
que o cliente já grava neste PC. Roda inteiro no `127.0.0.1`, só com a biblioteca padrão do
Python, sem rede e sem conta em serviço nenhum.

## O que ele faz

- **Acompanha o `Player.log` enquanto você joga** e grava cada partida assim que ela avança.
  Isso não é conveniência: o Arena **apaga o `Player.log` a cada vez que o cliente abre**.
  Sem acompanhamento, a sessão que você acabou de jogar some quando você reinicia o jogo.
- **Reconstrói cada jogo** turno a turno — mão conhecida, campo, pilha, vida, ações — e
  marca explicitamente onde a reconstrução tem lacuna.
- **Monta a linha do tempo** do que aconteceu (compras, terrenos, mágicas, dano, revelações)
  a partir das anotações do protocolo, não só das fotografias do estado.
- **Conta o seu grimório** em qualquer instante da partida e dá a probabilidade da próxima
  compra e das três seguintes.
- **Lista o que o adversário mostrou** — cartas e cores. Não nomeia arquétipo: é o que foi
  visto, não o deck dele.
- **Analisa o deck**: curva, base de mana contra a tabela publicada do Karsten, custo em
  curingas, e taxa de vitória por carta na mão com o intervalo de confiança ao lado.
- **Guarda notas e hipóteses** suas por posição e por deck.

O que ele **não** faz: não diz qual era a jogada certa, não atribui probabilidade de vitória
a linhas alternativas e não chama IA nenhuma. Não existe hoje nada público que avalie linha
de jogo em Magic com garantia — o jogo é Turing-completo, não há oráculo. O app entrega o
contexto sanitizado para você revisar; a leitura é sua.

## Requisitos

1. **Python 3.14** no PATH (nenhum pacote externo).
2. **Logs detalhados ligados no Arena**: engrenagem → *Adjust Options* → *View Account* →
   marcar **Detailed Logs (Plugin Support)** → reiniciar o cliente. Sem isso o log não tem
   registro de protocolo nenhum e o app não tem o que ler. A tela de Estatísticas mostra se
   estão ligados.

## Como abrir

Duplo clique em `Abrir MTGA Coach.cmd`, ou:

```
python run.py                                   # http://127.0.0.1:18731
python run.py --import-current                  # já importa o log configurado ao subir
python run.py --smoke                           # verificação rápida do servidor
```

O servidor só aceita `127.0.0.1`; qualquer outro endereço é recusado na inicialização.

## Fluxo de uso

1. Abra o app **antes** de jogar e clique em **Acompanhar partidas**. Deixe a aba aberta.
2. Jogue. As partidas aparecem na lista conforme avançam.
3. Abra uma partida, ande pelos quadros (setas ← →), e use as abas da barra lateral:
   *Decisão*, *Biblioteca*, *Adversário*, *Linha do tempo*.
4. Registre a nota do que quer revisitar. Ela volta na aba **Treino**.
5. Em **Decks**, vincule a composição observada ao deck salvo no Arena (o log não faz esse
   vínculo sozinho) e leia a base de mana e o custo em curingas.
6. Em **Estatísticas**, veja a taxa por modo, por quem começou, e o tamanho da amostra.

**Importar logs** lê o `Player.log` atual inteiro (em blocos, sem carregar na memória).
**Enviar log** aceita um arquivo que você escolher, até 64 MB. Reimportar o mesmo arquivo não
duplica nada: a identidade é o SHA-256 do conteúdo.

## Onde ficam os dados

Tudo em `%LOCALAPPDATA%/mtga-coach/`:

- `reviews.sqlite3` — partidas, quadros comprimidos, eventos, notas e hipóteses.
- `sources/` — cópias de log que você tenha enviado manualmente.

O identificador da conta e o da partida são gravados **em hash**; o nome do adversário é
guardado porque aparece na tela. Nada sai desta máquina.

Os quadros vão comprimidos em blocos: uma partida de 19 turnos ocupa cerca de 2% do JSON
cru. Na amostra de teste, oito partidas passaram de 25,5 MB para menos de 1 MB.

## Limites conhecidos

- **BO3 não foi conferido contra uma partida real.** O modelo de dados separa jogo e
  confronto desde o início, mas nenhuma captura BO3 real passou pelo app ainda. Até isso
  acontecer, o BO3 é código não validado.
- **A coleção não está no log.** O Arena parou de publicar as cartas que você possui, então
  o custo em curingas é o da lista inteira, não o que falta comprar.
- **O log não liga a lista jogada ao deck salvo.** O vínculo é seu, feito uma vez por
  composição, e fica gravado.
- **Mão inicial no BO1 não é aleatória.** A Wizards declara que o BO1 escolhe a mão entre
  cópias embaralhadas do deck, puxando para a proporção média de terras, sem publicar o
  critério. Comparar uma taxa de mão inicial medida no BO1 com o hipergeométrico é errado, e
  o app diz isso onde mostra o número.
- **Amostra pessoal é pequena.** Separar 55% de 50% com 95% de confiança e 80% de poder
  exige cerca de 1.565 partidas por braço. O app mostra o intervalo de Wilson ao lado de toda
  taxa justamente para não deixar 20 partidas parecerem um veredito.
- **Sem integração de IA.** Fica para uma decisão separada de credencial e orçamento.

## Fontes de método

- Frank Karsten, *How Many Sources Do You Need to Consistently Cast Your Spells? A 2022
  Update* (TCGplayer) — tabela de fontes por cor, 60 cartas, 90% de consistência, mulligan de
  Londres modelado.
- Frank Karsten, *How Many Lands Do You Need in Your Deck? An Updated Analysis* (TCGplayer) —
  a regressão `19,59 + 1,90 × valor de mana médio`.
- Wizards of the Coast (out/2018, esclarecido em mai/2019) — declaração sobre a mão inicial
  do BO1.
- Intervalo de Wilson e teste de duas proporções: estatística padrão, calculada localmente.

## Fronteira com a Wizards

O app lê um arquivo local que a própria Wizards criou para plugins de terceiros, com a opção
ligada pelo usuário, e não faz mais nada: não injeta memória, não intercepta rede, não
automatiza jogada, não envia dado a lugar nenhum. Uso pessoal, não comercial, na linha da
Fan Content Policy. Não é produto oficial nem endossado pela Wizards.

## Desenvolvimento

```
python -m unittest discover -s tests -t .      # 67 testes
node tests/test_ui.mjs                         # funções puras da interface
python -m compileall -q src
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launch_mtga_coach.ps1 -SmokeTest
```

Especificação e plano em `docs/specs/001-revisao-de-decisoes/`. Comparação com o estado da
arte e as lacunas que sobraram em `docs/analise/`.

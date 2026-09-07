# MTGA Coach × estado da arte dos apps de MTG Arena

Data: 07/09/2026. Base: leitura do código em `src/mtga_coach/`, medição contra o snapshot real
de log em `%LOCALAPPDATA%/mtga-coach/sources/0125891a…log` (9,7 MB, 3 partidas, 2.265 registros)
e levantamento dos produtos concorrentes e das fontes de método.

## 1. Onde o app estava

O que existia funcionava e passava nos 26 testes, mas parava antes do que o produto promete:

| Achado | Evidência | Consequência |
|---|---|---|
| **O log é apagado a cada reinício do Arena** e o app só importava sob demanda | Às 17h15 de 06/09 o `Player.log` tinha 60.771.213 bytes; às 21h04, depois de o cliente reiniciar, tinha 510.065 e o conteúdo anterior não estava no `Player-prev.log` | As partidas do dia se perdiam. Este é o defeito que invalida o produto inteiro |
| Teto de importação de 32 MB | `MAX_LOG_BYTES = 32 * 1024 * 1024` | O log de 60 MB daquele dia não importava; a interface só dizia "requisição inválida" |
| `annotations` do protocolo ignoradas por completo | 554 mensagens com 2.001 anotações no snapshot, nenhuma lida | Sem compras, dano, vida, revelações, sacrifícios — ou seja, sem "o que aconteceu" |
| `AnnotationType_ObjectIdChanged` ignorado | 181 ocorrências | O Arena renumera um objeto e o app perdia a identidade da carta atravessando a renumeração |
| Modo BO1/BO3 adivinhado pelo nome do evento | `match_mode("Historic_Ladder")` | O protocolo já traz `gameInfo.matchWinCondition`, que é autoritativo |
| `finalMatchResult` não lido | 2 ocorrências no snapshot | 2 dos 8 jogos gravados ficaram `status: complete` com `result: unknown` |
| Assento próprio deduzido do `ConnectResp` | exige exatamente um `systemSeatIds` | O `authenticateResponse.clientId` cruzado com `reservedPlayers[].userId` é direto e sempre disponível |
| Um jogo inteiro num único JSON | 4,87 MB para uma partida de 19 turnos; 25,5 MB de banco para 8 partidas | `/api/summary` levava 0,62 s com 8 partidas porque desserializava tudo; a ~500 partidas o banco passaria de 1 GB |
| `/api/games/{id}` devolvia 2,3 MB ao navegador | medido | Replay lento e slider de 538 posições |
| Sem nome de deck, sem rank, sem quem começou, sem mulligan, sem adversário | campos presentes no log e não lidos | Faltava tudo que os concorrentes mostram na primeira tela |

## 2. O que o mercado faz

Levantamento dos produtos vivos em 2026.

| Produto | Overlay ao vivo | Replay / revisão | Dados | Preço |
|---|---|---|---|---|
| **Untapped.gg** (HearthSim) | rico: cartas restantes, probabilidade de compra, cartas reveladas do adversário | histórico e matchup; **sem replay** | telemetria fechada, sem dataset público | grátis / US$ 7,99 mês |
| **MTG Arena Tool** (código aberto) | lista, cartas restantes, log de ações, relógio | **tem "replayer"** — reprodução passiva do que aconteceu | aberto, metadados do Scryfall | grátis |
| **Arena Tutor** (Draftsim) | overlay de draft com recomendação, cartas jogadas | resumo pós-jogo | fechada | grátis (Overwolf) |
| **Aetherhub Assistant** | cartas restantes e porcentagem | histórico | telemetria própria | grátis (Overwolf) |
| **17Lands** | nenhum (só envia log) | não | **datasets públicos em bulk** | grátis |
| **MTGA Pro Tracker** | era rico | histórico | fechada | **morto — repositório arquivado em 03/04/2025** |
| **MTGGoldfish Companion** | não (só sincroniza coleção) | não | fechada | grátis / US$ 6 mês |

Três conclusões que orientam o produto:

1. **Ninguém faz revisão de decisão de verdade.** O replay do MTG Arena Tool reproduz a
   sequência; não há, em nenhum produto, análise de linha alternativa, probabilidade de
   vitória por jogada ou marcação de erro. A Wizards também não tem replay nativo — é pedido
   aberto de anos no fórum oficial. **A lacuna que este app persegue está genuinamente vazia.**
2. **"IA" nos concorrentes é modelo de ranking, não LLM.** O Draftsmith do Untapped e a "AI"
   do Arena Tutor são modelos estatísticos de escolha de carta em draft. Nenhum produto com
   base de usuários usa LLM para conselho de jogada.
3. **A base é a mesma para todos.** Todos leem o mesmo `Player.log` com os logs detalhados
   ligados, e todos precisam de uma fonte de metadados de carta. O diferencial não está no
   acesso ao dado — está no que se faz com ele.

E um limite honesto sobre o teto do produto: **não existe avaliador de linha de jogo em
Magic**. O jogo é Turing-completo (Churchill, Biderman e Herrick, FUN 2021), então não há
oráculo geral de "jogada ótima" nem em princípio. O que a literatura tem são MCTS com
determinização para informação oculta (Cowling, Ward e Powley, 2012), LLMs afinados para
*draft* (UrzaGPT, 2026: 66% de acerto de escolha) e benchmarks de RL. Nada disso resolve
"esse ataque foi erro". Prometer isso seria mentira; o app não promete.

## 3. O que foi feito nesta rodada

### Correções que destravam o uso

- **`capture.py`**: seguidor do `Player.log` com detecção de rotação. Lê para a frente, percebe
  quando o cliente reiniciou (o arquivo encolheu ou os bytes iniciais mudaram), fecha a sessão
  anterior e abre outra. Importa o `Player-prev.log` uma vez ao ligar. Botão *Acompanhar
  partidas* na barra superior.
- **`scanner.py`**: leitor incremental que segura um registro partido ao meio entre duas
  leituras até os bytes restantes chegarem, decodifica UTF-8 através da fronteira dos blocos e
  mantém a numeração de linha ao descartar o que já consumiu. Um dicionário de diagnóstico do
  Unity que não é JSON deixa de interromper a varredura — antes, um `{` solto podia parar a
  leitura do arquivo inteiro.
- **Teto de importação**: o arquivo configurado passa a ser lido em blocos de 4 MB, sem
  carregar na memória. O envio pelo navegador continua com teto (64 MB), porque esse caminho
  segura o corpo inteiro.

### Fatos que o log já dava e não eram lidos

`gameInfo.matchWinCondition` (modo), `finalMatchResult.resultList` (resultado e motivo:
concessão × fim de jogo), `authenticateResponse.clientId` × `reservedPlayers[].userId`
(assento e adversário), `turnInfo.activePlayer` no turno 1 (quem começou), `MulliganReq`
(mulligans por assento), `constructedClass`/`constructedLevel` (rank), `InventoryInfo`
(curingas), e as listas de deck nomeadas — filtrando os 775 precons de catálogo que o log
também despeja, para ficar com os 20 decks reais do jogador.

### Camadas novas

- **`timeline.py`** — anotações viram eventos legíveis: compra, terreno jogado, mágica
  conjurada, resolução, saída do campo, descarte, exílio, anulação, dano, vida, revelação,
  ficha, embaralhamento, marcador, ação do jogador. Um objeto que o jogador não podia ver gera
  evento **sem identidade**, que é exatamente o que ele sabia na hora.
- **`protocol.py`** — vocabulário do protocolo em um lugar só. Valor desconhecido mantém a
  forma bruta em vez de ganhar rótulo inventado.
- **`analysis.py`** — hipergeométrico exato, intervalo de Wilson, tamanho de amostra por teste
  de duas proporções, curva, fontes por cor, tabela publicada do Karsten e a regressão de
  contagem de terras.
- **Lineage de objeto** no redutor: `orig_id → new_id` é aplicado antes de projetar o estado,
  então a carta atravessa a renumeração sem virar fantasma nem perder o nome.
- **Janela de estados limitada** (96): o redutor guardava todo estado do jogo, e 9,7 MB de log
  retinham 88 MB de memória. Um predecessor mais antigo que a janela vira lacuna declarada, não
  chute.

### Armazenamento

Esquema normalizado, com migração automática da versão 1. Quadros em blocos de 25 comprimidos
com `zlib`; navegação por um índice leve de quadros; eventos em tabela própria.

| Medida | Antes | Depois |
|---|---|---|
| Banco com 8 partidas | 25,5 MB | 0,98 MB |
| `/api/summary` | 0,62 s | 0,001 s |
| `/api/games/{id}` | 2,3 MB | 116 KB |
| Um bloco de 25 quadros | — | 1,6 ms |

### O que a interface passou a mostrar

Aba lateral do replay com *Decisão*, *Biblioteca* (grimório restante e probabilidade de compra),
*Adversário* (o que ele mostrou e em que cores) e *Linha do tempo* clicável. Tela de Decks com
curva, exigência de fontes por cor contra a tabela do Karsten nomeando a carta que puxa a
exigência, custo em curingas cruzado com o estoque lido do log, e taxa de vitória por carta na
mão com intervalo. Tela de Estatísticas com rank, quem começou, mulligans e saúde da base.

Medido na amostra real: o deck Dimir roda **19 terras** com valor de mana médio 1,83, contra
**23,07** da regressão publicada; e a Contramágica ({U}{U} no turno 2) pede **21 fontes azuis**
contra as **13** que a lista tem. São dois achados acionáveis que o app antes não conseguia
enunciar.

## 4. O que ficou de fora, e por quê

1. **BO3 real.** Nenhuma captura BO3 passou pelo app. O modelo separa jogo e confronto desde o
   início e há teste sintético, mas isso não é validação. É o primeiro item da próxima rodada.
2. **Overlay durante a partida.** Os concorrentes mostram o grimório restante *enquanto* você
   joga, em cima do jogo. O app calcula tudo isso, mas só na revisão. Um overlay exige janela
   sempre-visível — decisão de empacotamento, não de dados.
3. **Metagame externo.** Classificar o deck do adversário por arquétipo exige base externa
   (MTGGoldfish, Aetherhub, MTGTop8 — nenhum com API pública documentada e termos claros). O app
   mostra as cartas vistas e as cores, e diz que não é o deck dele.
4. **Draft.** O log traz as escolhas de draft e o 17Lands publica datasets em bulk com licença
   a conferir. Fora do escopo do uso atual (Historic construído).
5. **Vínculo automático lista → deck salvo.** O log não faz esse vínculo. O `LastPlayed` do deck
   permite um palpite por horário; preferi o vínculo explícito do usuário, gravado uma vez.
6. **IA.** Depende de decisão de credencial e orçamento. Quando entrar, o caminho seguro é o
   que a literatura sustenta: a IA explica em português um número que o app já calculou de
   forma determinística, não lê o tabuleiro por conta própria — a taxa de erro de LLM em regra
   e estado de jogo em Magic é alta e documentada.
7. **Simulador de "goldfish".** "Com que frequência este deck cumpre a curva até o turno 4"
   pede simulação de mão e compra. É viável em Python puro e é a extensão natural do módulo de
   análise.

## 5. Ordem sugerida

1. Capturar um BO3 real e conferir jogo × confronto e sideboard. *(bloqueia qualquer alegação
   sobre BO3)*
2. Rodar uma sessão inteira com o acompanhamento ligado e conferir memória e banco ao fim.
3. Simulador de consistência de curva.
4. Overlay ou janela empacotada, se o uso diário pedir.
5. Só então IA, com o recorte acima.

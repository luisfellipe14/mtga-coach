# MTGA Coach — descoberta dos logs locais

Data: 06/09/2026. Estado: investigação de dados concluída para a amostra; reconstrução de replay ainda não implementada.

## Objetivo confirmado pelo usuário

Criar um app pessoal para revisar decisões e ajustar decks a partir dos logs do MTG Arena. Funções já presentes no Untapped podem entrar no produto. Uma eventual substituição da assinatura depende da utilidade comprovada do app.

## Amostra e evidência

Fonte: `%USERPROFILE%/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`.

Snapshot capturado às 13:41:38 UTC, com 9.705.971 bytes. SHA-256: `0125891a07c661c85f663abaf46fb384cbc342f098642823aa967b7b8304f0f7`.

Arquivo bruto preservado em `%LOCALAPPDATA%/mtga-coach/sources/<sha256>.log`, fora dos documentos do projeto. O arquivo de origem continuou a receber eventos durante a investigação; os números abaixo pertencem exclusivamente ao snapshot.

Evidência estruturada: [2026-09-06-log-audit.json](2026-09-06-log-audit.json). A extração percorreu objetos JSON e JSON serializado dentro de campos textuais, identificou mensagens GRE/cliente, associou atualizações ao último par matchID/gameNumber declarado e comparou gameStateId/prevGameStateId. Contagens de mensagens não equivalem a contagens de decisões únicas.

| Partida, identificador derivado | Evento declarado | Atualizações de estado | Maior turno observado | Estado no fim da amostra |
|---|---|---:|---:|---|
| fd45a990e0, jogo 1 | Historic_Ladder | 538 | 19 | GameOver / MatchComplete |
| e14289e494, jogo 1 | Historic_Ladder | 416 | 15 | GameOver / MatchComplete |
| 0b03f0ddf7, jogo 1 | Historic_Ladder | 93 | 5 | Play / GameInProgress |

Total observado: 1.047 registros de estado. As duas primeiras partidas registram início e término, mas essa presença não prova a completude semântica de todos os estados intermediários. A terceira partida estava em curso no momento da captura; não deve entrar como derrota nem como abandono.

As duas primeiras conexões registram a mesma composição de deck: 60 cartas principais, 23 IDs distintos e uma carta no campo sideboardCards. A terceira registra outra composição, com 60 cartas principais, 14 IDs distintos e sideboardCards vazio. Não houve classificação das cartas por nome, função, cor ou papel da carta no sideboard.

## O que o arquivo permite investigar

- Evolução de turnos, fases, prioridade e vida dos jogadores.
- Objetos e zonas: mão, campo de batalha, grimório, pilha, cemitério e exílio.
- Respostas de mulligan, ações executadas, seleção de alvos e declarações de ataque/bloqueio.
- Composição do deck e distinção entre listas por hash das cartas e quantidades.
- Desfecho declarado pelo jogo, sujeito à correta identificação do assento do usuário.

Esses são campos e eventos observados. Ainda não existe um replay visual conferido nem avaliação tática dessas partidas.

## Pontos que o leitor deve resolver

1. **Atualizações parciais.** O log contém estados Full e Diff. O leitor precisa aplicar as regras de atualização de cada campo, exclusões de objetos, zonas e anotações; uma fusão genérica de JSON não basta.
2. **Referências de busca.** Na segunda partida, os estados 31 e 139 referem os estados 30 e 138. A extração não localizou mensagens gameStateMessage com esses dois IDs. Encontrou referências aos mesmos IDs em ClientMessageType_SearchResp e GREMessageType_TimerStateMessage. Isso exige investigação das mensagens de busca; não prova perda definitiva de informação.
3. **Reconexão/ressincronização.** A primeira partida contém outro estado Full no turno 15. O comportamento de reposição do estado precisa de teste.
4. **Informação no instante da decisão.** Uma carta adversária revelada depois da decisão não pode entrar retroativamente no contexto do treinador. A mesma regra vale para compras futuras do próprio jogador.
5. **Texto das cartas.** A auditoria identificou IDs, mas não validou sua correspondência com nomes, faces, habilidades ou versões digitais rebalanceadas. A IA só deve receber posições com essa correspondência resolvida ou com a lacuna explícita.
6. **Fronteiras de coleta.** Reimportação, reinício do cliente, linha JSON incompleta e crescimento do arquivo precisam de tratamento antes da captura automática.

## Fontes externas consultadas

- [Wizards — Creating Log Files on PC/Mac/Steam](https://mtgarena-support.wizards.com/hc/en-us/articles/360000726823-Creating-Log-Files-on-PC-Mac-Steam), seções Creating Detailed Logs e Plain Text Log Files: instruções de logs detalhados, localização de Player.log e preservação da sessão anterior em Player-prev.log. Consulta em 06/09/2026.
- [Cliente público do 17Lands](https://github.com/rconroy293/mtga-log-client), README: confirma o uso dos logs do Arena como entrada para coleta. O cliente não foi executado nem incorporado ao projeto.
- As páginas de documentação de cartas/API do Scryfall responderam HTTP 403 nesta sessão. A integração com essa fonte permanece sem validação.

## Próxima entrega proposta

Um leitor que reconstrua e exponha a primeira partida encerrada, acompanhado de testes de estado, informação oculta e importação repetida. A segunda partida será o caso adversarial de busca de cartas; a terceira, o caso de arquivo capturado antes do desfecho. A especificação de produto está em [spec.md](../specs/001-revisao-de-decisoes/spec.md).

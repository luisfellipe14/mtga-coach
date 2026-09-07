# MTGA Coach — revisão de decisões e ajuste de deck

Status: proposta inicial de desenho, apoiada em logs reais. Finalidade e fonte inicial confirmadas pelo usuário; desenho detalhado sujeito à revisão. Nenhum app foi implementado nesta etapa.

## 1. Resultado desejado

Depois de jogar MTGA, o usuário abre o app, encontra suas partidas, revê uma decisão com as informações disponíveis naquele momento e registra o que pretende testar na próxima sessão. O histórico de versões do deck conecta os ajustes aos resultados observados.

O usuário aceita funções semelhantes às do Untapped e considera substituir a assinatura no futuro. O projeto deve permitir essa evolução. A primeira entrega precisa provar a reconstrução das partidas e a utilidade da revisão.

## 2. Decisões já confirmadas e premissas

- Confirmado: uso pessoal, revisão de decisões, ajustes de deck, logs como fonte inicial e ausência de veto a funcionalidades semelhantes às do Untapped.
- Confirmado em 06/09: suporte obrigatório a BO1 e BO3; BO1 concentra a maior parte do uso e terá prioridade na experiência inicial. BO3 integra o modelo de dados desde a primeira entrega.
- Observado: Arena instalado e ativo no Windows deste PC; o Player.log contém partidas de Historic_Ladder, composições de deck, mensagens de decisão e atualizações do estado.
- Premissa de trabalho: Histórico como primeiro conjunto de testes, porque a amostra real já existe. O formato de cartas prioritário ainda não foi confirmado pelo usuário; essa premissa não exclui outros formatos.
- Nome provisório: MTGA Coach. Documentos em `C:/Users/l_fel/Projects/mtga-coach`; dados privados em `%LOCALAPPDATA%/mtga-coach`.

## 3. Opções de entrega

| Opção | Benefício | Custo ou limite |
|---|---|---|
| Aplicação local com interface no navegador — recomendada | Leitura automática dos logs por processo local; interface rica; histórico no PC | O processo local deve estar ativo para acompanhar novos eventos |
| Aplicação desktop empacotada | Janela própria e instalação integrada | Acrescenta trabalho de empacotamento e atualização antes da prova do replay |
| Site com upload de arquivo | Acesso pelo navegador, sem coletor instalado | Exige seleção/envio de logs; captura automática de arquivo local exige componente adicional |

A primeira opção permite avaliar o produto no PC e empacotar a mesma interface depois. A escolha de bibliotecas e versões pertence ao plano de implementação.

## 4. Fluxo de uso

1. **Partidas:** ver partidas encerradas e em curso, deck utilizado, formato e disponibilidade da reconstrução. Estatísticas excluem partidas sem desfecho conhecido.
2. **Revisão:** abrir uma partida e avançar por turno ou decisão. Ver mão conhecida, mesa, pilha, vida, ação escolhida e lacunas relevantes. Marcar uma decisão para estudo.
3. **Treinador:** comparar a decisão marcada com alternativas sustentadas pela posição, pelo texto das cartas e pelas ações disponíveis. Explicitar o que foi observado e o que é hipótese tática.
4. **Deck:** salvar uma alteração como nova versão, registrar a hipótese do ajuste e comparar amostras sob filtros equivalentes.
5. **Treino:** retornar a decisões marcadas sem expor a continuação antes da resposta do usuário; revisar o raciocínio depois.

## 5. Separação entre reconstrução, raciocínio e estatística

O leitor reconstrói fatos. O catálogo resolve IDs e versões de cartas. O treinador explica opções com base nesses insumos. O painel calcula resultados das partidas e versões de deck.

Uma derrota não recebe automaticamente o rótulo de erro. Uma sugestão do treinador não recebe o rótulo de jogada ótima sem verificação apropriada. O app não atribui porcentagens de vitória a linhas alternativas com base apenas em texto gerado por IA.

O contexto tático respeita o conhecimento do jogador no instante escolhido: mão própria e informação adversária já revelada, com tratamento explícito das informações desconhecidas. Resultado final e revelações futuras ficam fora desse contexto.

Nas estatísticas, partida individual e confronto BO3 são unidades distintas. Formato, versão do deck, período e quem começou a partida devem aparecer nos filtros quando os dados existirem. A classificação do deck adversário por cartas reveladas deve indicar a base e a incerteza; não equivale à lista completa do oponente.

BO1 e BO3 mantêm estatísticas e hipóteses de ajuste separadas. No BO3, a lista de inscrição e as configurações de cada jogo após sideboard são entidades distintas: uma troca entre os jogos 1 e 2 não cria automaticamente uma nova versão experimental do deck. O confronto agrega seus jogos e conserva seu próprio resultado.

Na revisão dos jogos 2 e 3, o treinador pode usar informações reveladas nos jogos anteriores do mesmo confronto. Deve distinguir uma carta vista no jogo anterior de uma carta confirmada na configuração ou mão atual; o sideboard pode alterar a composição adversária. Informação revelada no futuro continua excluída.

## 6. Entregas em sequência

### A. Prova do replay — primeira implementação

- Importar snapshot local e preservar sua origem.
- Identificar partidas, estado de encerramento e composição do deck.
- Representar BO1 e BO3 desde o início: confronto, jogo, modo de disputa e configuração do deck por jogo. Validar inicialmente a reconstrução visual com a amostra BO1 disponível.
- Reconstruir os estados relevantes da primeira partida da amostra e oferecer navegação por turnos/decisões.
- Expor lacunas, buscas e ressincronizações de modo verificável.
- Resolver IDs das cartas necessárias para esse caso de teste, inclusive faces e variantes aplicáveis.

### B. Revisão assistida

- Seleção de uma decisão e comparação de alternativas em português.
- Contexto restrito às informações disponíveis no instante selecionado.
- Fundamentação em ações registradas e textos de cartas; indicação de incerteza.
- Notas do usuário, marcações e exercícios de repetição.
- No BO3, revisão das trocas de sideboard e da adaptação ao que o adversário revelou nos jogos anteriores.

Fornecedor de IA, integração e orçamento por revisão ficam para a definição desta entrega. A prova do replay não depende de uma API de IA.

### C. Ajuste e acompanhamento de decks

- Histórico de listas e diferenças entre versões.
- Configurações pós-sideboard ligadas ao confronto e ao jogo; versões experimentais registradas separadamente.
- Resultados pessoais por versão e contexto, com tamanho da amostra visível.
- Hipótese de ajuste, comparação entre amostras e decisão do usuário sobre manter/reverter a mudança.
- Expansão posterior para coleção, custo de curingas e fontes externas de metagame, conforme disponibilidade dos dados.

### D. Captura automática e conveniência

- Acompanhamento do arquivo enquanto o app está aberto, recuperação após reinício e importação idempotente.
- Captura de partidas futuras com a mesma estrutura usada na importação manual.
- Empacotamento desktop se melhorar o uso diário.

## 7. Critérios de aceitação da entrega A — EARS

- **AC01:** WHEN o usuário importar duas vezes o mesmo snapshot, THE SYSTEM SHALL manter uma única ocorrência de cada partida e estado já identificado.
- **AC02:** WHEN uma partida tiver início e desfecho registrados, THE SYSTEM SHALL identificá-los e mostrar a origem de ambos sem presumir completude dos estados intermediários.
- **AC03:** WHILE uma partida não tiver desfecho conhecido, THE SYSTEM SHALL mantê-la fora do denominador de vitórias/derrotas.
- **AC04:** WHEN o leitor receber um estado Full, THE SYSTEM SHALL reconstruir o estado conforme a semântica do protocolo, com teste para início e ressincronização.
- **AC05:** WHEN o leitor receber um estado Diff, THE SYSTEM SHALL aplicar alterações e exclusões conforme a semântica de cada campo e conservar a origem do estado resultante.
- **AC06:** IF uma referência anterior não puder ser resolvida, THE SYSTEM SHALL marcar a confiança da reconstrução e impedir análise tática que dependa do trecho não resolvido, até sua recuperação verificável.
- **AC07:** WHEN uma carta for revelada depois da decisão selecionada, THE SYSTEM SHALL excluí-la do contexto histórico dessa decisão, salvo se já fosse conhecida por evento anterior verificável.
- **AC08:** IF um ID de carta ou variante não puder ser resolvido, THE SYSTEM SHALL indicar o ID e a lacuna, sem inventar nome, texto ou habilidade.
- **AC09:** WHEN duas listas de inscrição diferirem em carta ou quantidade, THE SYSTEM SHALL distinguir suas versões, preservando a associação de cada partida à composição observada; mudanças entre jogos do mesmo BO3 devem ser registradas como configurações pós-sideboard, sem criar automaticamente uma nova versão experimental.
- **AC10:** WHEN uma posição for exibida, THE SYSTEM SHALL permitir rastrear turno, vida, zonas conhecidas e ação ao snapshot e aos eventos correspondentes.
- **AC11:** IF o arquivo terminar em um registro incompleto, THE SYSTEM SHALL preservar os registros anteriores válidos e indicar que o último registro aguarda complemento ou nova importação.
- **AC12:** WHEN houver exportação de uma posição para revisão, THE SYSTEM SHALL excluir credenciais, identificadores de conta desnecessários, hover de interface e dados fora do recorte escolhido.
- **AC13:** WHEN o arquivo contiver vários jogos de um confronto BO3, THE SYSTEM SHALL agrupá-los pelo identificador de confronto e distinguir cada jogo por seu identificador ou número, preservando resultados de jogo e confronto separadamente.
- **AC14:** IF o modo BO1/BO3 não puder ser determinado por metadado ou evidência verificável, THE SYSTEM SHALL marcá-lo como desconhecido e excluir o registro de comparações específicas por modo; a quantidade de cartas no sideboard não determina o modo.
- **AC15:** WHEN o usuário consultar resultados de BO3, THE SYSTEM SHALL distinguir resultados de jogos e confrontos e separar jogo 1 de jogos 2/3, sem misturar esses denominadores com BO1.
- **AC16:** WHEN uma posição dos jogos 2/3 for preparada para revisão, THE SYSTEM SHALL identificar separadamente informações reveladas antes no confronto e informações confirmadas no jogo atual, sem atribuir automaticamente as primeiras à mão ou configuração atual do adversário.

Validação: fixtures sintéticas para erros, informação futura e agrupamento BO3; amostra real disponível para início, término, ressincronização, busca de cartas, versões de deck e partida em curso. A captura e conferência de um confronto BO3 real permanecem necessárias antes de declarar a reconstrução desse modo validada. Um replay visual bonito não satisfaz os critérios sem conferência do estado.

## 8. Limites da descoberta atual

A auditoria identificou mensagens e relações entre estados. Não implementou o redutor de estados, não validou o tabuleiro visual, não avaliou jogadas e não calculou qualidade de decks. Nenhuma integração com Untapped foi confirmada.

O app pode construir estatísticas pessoais a partir do histórico capturado. Estatísticas amplas do metagame exigem fonte externa adequada ou uma base maior de partidas. Os logs deste PC não substituem, por si, uma base global.

Evidência e questões técnicas: [descoberta dos logs](../../discovery/2026-09-06-viabilidade-logs.md) e [auditoria JSON](../../discovery/2026-09-06-log-audit.json).

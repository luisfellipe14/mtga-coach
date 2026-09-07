# O heurístico de texto de carta não funciona como sinal de qualidade

**Data:** 2026-09-07 · **Script:** `scripts/backtest_pick.py` · **Semente:** 20260907

## O que foi medido

O ranking de pack tem três fontes possíveis — taxa medida do 17Lands, nota assinada da
comunidade, e o texto da própria carta. A terceira é a que roda em set recém-lançado, e era
a única que ninguém tinha conferido.

A pergunta medida é a que o jogador faz: **se eu pegar a carta que o heurístico gosta em vez
da que o dado gosta, quanto de taxa de vitória isso me custa por pick?**

## Restrição encontrada no caminho

O endpoint público do 17Lands serve **apenas o set que está em fila no momento**. DSK, FIN,
TDM e EOE respondem com todas as cartas e `game_count: 0`. Isso significa duas coisas:

1. O backtest só tem uma verdade disponível — o set em rotação. Aqui, HOB, com **54 cartas**
   acima de 300 jogos. A amostra é essa e não dá para aumentá-la.
2. O app **nunca** terá número do 17Lands para um set fora de rotação. O heurístico não é um
   remendo de duas semanas; é o que roda na maior parte do tempo.

## Resultado

20.000 packs de 14 cartas sorteados das 54:

| medida | valor | referência |
|---|---|---|
| correlação de postos (Spearman) | **0,084** | 0 = nenhuma relação |
| acerta a melhor carta do pack | **8,8%** | 7,1% é o acaso em 14 cartas |
| perda média por pick | **4,20 pp** | escolher às cegas perde 4,78 pp |
| do vão entre cego e perfeito, recupera | **12,1%** | |

E, medido separadamente porque é outra tarefa — escolher 23 de um pool de 45:

| medida | valor |
|---|---|
| ganho do 23 escolhido por estrutura sobre a média do pool | **−0,03 pp** |
| ganho do 23 medido como melhor | **+2,40 pp** |
| do ganho disponível, recupera | **0%** |

## Conclusão

**O texto da carta não distingue qualidade.** Não como ordem de pick (recupera 12% do vão,
com correlação estatisticamente indistinguível de zero nesta amostra) e não como seleção de
deck (recupera 0%: as 23 cartas que ele escolhe valem o mesmo que 23 sorteadas do pool).

Ele acerta o que é fácil — reconhece uma remoção, reconhece uma criatura — e isso não é
qualidade. Numa amostra de 54 cartas, "remoção" não separa a boa da ruim.

## O que muda no produto

1. Sem dado medido, o app **não nomeia uma pick**. Mostra o pack com custo, tipo e encaixe
   de cor, e diz que uma ordem ali não seria melhor que adivinhar — com este número ao lado.
2. O construtor de deck continua montando as 23, porque o jogador precisa de um deck legal,
   com curva e na cor. Mas ele escolhe por **forma** — contagem de criaturas e curva — e diz
   que não está afirmando qualidade de carta.
3. O que sobrevive intacto: a leitura de cor do pool, o ajuste de encaixe de cor, a contagem
   de terrenos e a checagem de fontes. Nada disso é afirmação sobre qualidade de carta; é
   aritmética sobre o pool.
4. É exatamente aqui que a nota assinada da comunidade (`community/`) deixa de ser ideologia
   e vira necessidade: é a única fonte de qualidade disponível para um set fora de rotação.

## Limite desta medição

54 cartas, um set, um formato. É pouco, e não dá para ser mais. O que a amostra sustenta é a
conclusão negativa — se o heurístico funcionasse bem, 54 cartas bastariam para mostrar sinal,
e não há sinal. O que ela não sustenta é qualquer afirmação sobre *quanto* ele é ruim.

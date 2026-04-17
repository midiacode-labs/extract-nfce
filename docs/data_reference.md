[cite_start]Com base no manual de especificações técnicas do DANFE NFC-e, a hierarquia de dados na impressão da nota é organizada em **nove divisões principais**[cite: 16, 58]. [cite_start]Essa estrutura garante uma representação simplificada e padronizada da transação de venda no varejo[cite: 42].

A organização segue esta ordem lógica de cima para baixo:

### 1. Cabeçalho (Divisão I)
[cite_start]Contém a identificação do emitente e o título do documento[cite: 130, 131].
* [cite_start]**CNPJ do Emitente** ou **CPF do Emitente** (formatados com máscara)[cite: 132].
* [cite_start]**Razão Social ou Nome do Emitente**[cite: 133].
* [cite_start]**Endereço Completo do Emitente** (sem indicação de país)[cite: 134].
* [cite_start]**Texto fixo:** "Documento Auxiliar da Nota Fiscal de Consumidor Eletrônica"[cite: 135].
* [cite_start]**Logotipo:** Opcional, no canto esquerdo[cite: 136].

---

### 2. Detalhes de Produtos/Serviços (Divisão II)
[cite_start]Tabela detalhada dos itens vendidos[cite: 161]. [cite_start]Caso a legislação estadual permita e o consumidor aceite, esta divisão pode ser suprimida na versão "resumida" ou "ecológica"[cite: 45, 162, 163].
* [cite_start]**Código:** Código do produto adotado pelo estabelecimento[cite: 165].
* [cite_start]**Descrição:** Descrição do produto ou serviço[cite: 165].
* [cite_start]**Qtde:** Quantidade de unidades adquiridas[cite: 166].
* [cite_start]**UN:** Unidade de medida[cite: 167].
* [cite_start]**Vl Unit:** Valor unitário[cite: 168].
* [cite_start]**Vl Total:** Valor total do item[cite: 169].

---

### 3. Informações de Totais (Divisão III)
[cite_start]Resumo financeiro da nota[cite: 175, 186].
* **Qtde. [cite_start]Total de Itens:** Somatório da quantidade de itens distintos[cite: 187].
* [cite_start]**Valor Total R$:** Somatório dos valores totais dos itens[cite: 188].
* [cite_start]**Desconto R$:** Exibido apenas se houver[cite: 189].
* [cite_start]**Acréscimos (Frete, Seguro, Outras Despesas):** Exibidos apenas se houver[cite: 189].
* [cite_start]**Valor a Pagar R$:** Valor total final (Soma dos itens + acréscimos - descontos)[cite: 239].
* [cite_start]**Forma de Pagamento:** Ex: Dinheiro, Cartão de Crédito, etc.[cite: 240].
* [cite_start]**Valor Pago:** Valor efetivamente pago em cada forma[cite: 241].
* [cite_start]**Troco:** Valor do troco (obrigatório em versões recentes)[cite: 242, 244].

---

### 4. Consulta via Chave de Acesso (Divisão IV)
[cite_start]Instruções para consulta manual no portal da SEFAZ[cite: 245].
* [cite_start]**Texto fixo:** "Consulte pela Chave de Acesso em" seguido da URL da SEFAZ[cite: 246].
* [cite_start]**Chave de Acesso:** Impressa em 11 blocos de quatro dígitos[cite: 246].

---

### 5. Consulta via QR Code (Divisão V)
[cite_start]Área reservada para a imagem do QR Code[cite: 248, 249].
* [cite_start]Pode ser impresso à esquerda das informações do consumidor ou centralizado[cite: 250].
* [cite_start]**Tamanho mínimo:** $25mm \times 25mm$[cite: 250, 519].

---

### 6. Informações sobre o Consumidor (Divisão VI)
[cite_start]Identificação de quem adquiriu os produtos[cite: 278].
* **CONSUMIDOR CNPJ/CPF/Id. [cite_start]Estrangeiro:** Obrigatório para valores $\ge$ R$ 10.000,00 ou entregas em domicílio[cite: 279, 280, 281, 282].
* [cite_start]**Mensagem:** "CONSUMIDOR NÃO IDENTIFICADO" caso o cliente não queira ser identificado (dentro dos limites legais)[cite: 289].
* [cite_start]**Nome e Endereço:** Opcionais, mas obrigatórios para entrega em domicílio[cite: 287, 288].

---

### 7. Identificação da NFC-e e Protocolo de Autorização (Divisão VII)
[cite_start]Dados técnicos de validação fiscal[cite: 290, 291].
* [cite_start]**NFC-e nº:** Número da nota[cite: 292].
* [cite_start]**Série:** Série da nota[cite: 293].
* [cite_start]**Data e Hora de Emissão:** Convertida para o horário local[cite: 294].
* [cite_start]**Protocolo de Autorização:** Número e data/hora fornecidos pela SEFAZ (suprimido em emissões offline/contingência)[cite: 295, 297].

---

### 8. Área de Mensagem Fiscal (Divisão VIII)
[cite_start]Mensagens de interesse do Fisco[cite: 298, 299].
* [cite_start]**Contingência:** Se aplicável, deve destacar o texto "EMITIDA EM CONTINGÊNCIA Pendente de autorização" abaixo do cabeçalho e da identificação da nota[cite: 300, 301, 302, 303].
* [cite_start]**Tributos Totais Incidentes (Lei Federal 12.741/2012):** Valor aproximado da carga tributária[cite: 111, 414, 415].

---

### 9. Mensagem de Interesse do Contribuinte (Divisão IX)
[cite_start]Parte final do documento para mensagens institucionais ou complementares cadastradas no XML[cite: 411, 412].
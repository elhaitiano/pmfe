# PMFE

**Plataforma de Monitorização de Formação EPAL**, no âmbito do projecto PROÁGUA.

A PMFE reúne o cadastro de técnicos, o planeamento de formações, as inscrições, as presenças, os resultados e a entrega de certificados. Permite acompanhar o percurso formativo de cada técnico e produzir indicadores e relatórios para apoiar a gestão.

A aplicação funciona localmente num único computador, através do navegador, com interface em português. Após a instalação das dependências, as funcionalidades principais estão disponíveis sem ligação à Internet.

## Funcionalidades

| Módulo | O que permite fazer |
| --- | --- |
| Cadastro de técnicos | Pesquisar por nome ou número mecanográfico, criar e corrigir fichas, consultar direcção, categoria e contactos. |
| Histórico de formação | Consultar e actualizar participações, duração, presenças, notas, resultados e entrega de certificados. |
| Ficha individual | Consultar dados e indicadores do técnico, visualizar o histórico e imprimir ou guardar a ficha em PDF pelo navegador. |
| Catálogo de formações | Criar acções com categoria, objectivos e duração prevista; editar ou arquivar acções existentes. |
| Planeamento e turmas | Organizar grupos G1, G2… por acção, com capacidade, local, formador, horários e calendário de dias efectivos, incluindo dias não consecutivos. |
| Inscrições | Seleccionar participantes, rever avisos de histórico, justificar repetições e transferir inscrições entre grupos ainda não iniciados. |
| Actualização colectiva | Registar presenças verificadas, notas, resultados e certificados de uma mesma edição, com revisão antes da confirmação. |
| Listas de presença | Gerar listas diárias em PDF ou Excel segundo o modelo institucional, com espaços para assinatura. |
| Dashboard executivo | Consultar indicadores e gráficos, filtrar por período, direcção, área e situação, e explorar os registos correspondentes. |
| Relatórios e arquivo | Pré-visualizar, gerar, arquivar e descarregar relatórios PDF por período ou direcção. |
| Assistente EPAL | Pesquisar orientações locais, seguir passos e utilizar atalhos. Os administradores podem editar e publicar os guias. |
| Certificados e documentos privados | Distinguir emissão, declaração e entregas ao participante/RH; anexar CV, certificados e comprovativos PDF até 10 MB, com acesso administrativo. |
| Cópias de segurança e auditoria | Exportar a base, os relatórios e os documentos privados, conservar cópias antes das migrações e consultar o registo das operações administrativas. |

A eliminação de turmas preparatórias e de acções criadas por engano exige acesso administrativo, motivo e confirmação `ELIMINAR`. Turmas com histórico e acções com grupos ou histórico associado ficam protegidas contra eliminação.

## Percurso de utilização

1. Criar ou seleccionar uma acção no catálogo.
2. Criar um grupo e definir dias, horário, capacidade, local e formador.
3. Seleccionar os técnicos, rever os avisos e confirmar as inscrições.
4. Gerar a lista de presença de cada dia em PDF ou Excel.
5. Iniciar o grupo para criar as participações no histórico.
6. Actualizar presenças verificadas, notas, resultados e entregas de certificados.
7. Consultar indicadores, produzir relatórios e guardar cópias de segurança.

## Regras de tratamento dos dados

- Uma inscrição não confirma presença nem formação realizada.
- **Horas previstas:** dias de formação × horas por dia.
- **Horas realizadas:** dias de presença × horas por dia.
- **Assiduidade individual:** presenças ÷ dias previstos × 100.
- **Assiduidade consolidada:** soma das presenças ÷ soma dos dias elegíveis × 100.
- Uma nota zero é válida e distingue-se de uma nota não preenchida.
- O resultado da formação e a entrega do certificado são informações independentes.
- Zero presenças sem verificação não confirma uma ausência.
- Um PDF arquivado conserva os dados do momento da geração; alterações posteriores não reescrevem o documento.

## Tecnologias e arquitectura

| Componente | Tecnologia |
| --- | --- |
| Backend | Python 3.12, Django e Waitress |
| Interface | Templates Django, HTML, CSS/Tailwind já compilado e JavaScript |
| Serviço frontend | Nginx para ficheiros estáticos e encaminhamento de pedidos para Django |
| Base de dados | SQLite num volume Docker persistente |
| Documentos | ReportLab para PDF, openpyxl para importação Excel e modelo XLSX para listas de presença |
| Execução | Docker Compose |

```text
Navegador — http://localhost:8080
    │
    ▼
frontend: Nginx
    ├── CSS, JavaScript, imagens e fontes locais
    └── backend: Django + Waitress
                    └── volume database
                         ├── pmf.sqlite3
                         ├── secret.key
                         ├── reports/
                         ├── documents/ (PDFs privados)
                         └── backups/
```

Existem **dois serviços Docker**, `frontend` e `backend`. `database` é um volume persistente, não um terceiro contentor. Django gera as páginas HTML no servidor.

## Instalação

### Requisitos

- **Linux:** Docker Engine e Docker Compose instalados e em execução.
- **Windows:** Docker Desktop configurado para contentores Linux.
- Ligação à Internet na primeira construção, caso as imagens e dependências ainda não estejam disponíveis localmente.

Não é necessário instalar Python, Node.js ou Microsoft Excel no computador anfitrião para utilizar a aplicação com Docker.

### Primeiro arranque

Descarregue ou clone o repositório **pmfe**. A partir da pasta que contém `compose.yaml`, execute:

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose exec backend python manage.py createsuperuser
```

Abra **http://localhost:8080** e entre com a conta criada.

Um volume novo começa com uma base vazia. Os dados pessoais, as contas e as cópias de segurança não são distribuídos com o repositório. As migrações são aplicadas automaticamente no arranque do backend.

### Configuração local

O repositório chama-se `pmfe`. O ficheiro Compose define `pmf-containers` como nome de projecto por defeito. Para uma **nova instalação**, pode definir outro nome num ficheiro `.env`, junto de `compose.yaml`:

```dotenv
COMPOSE_PROJECT_NAME=pmfe
PMF_PORT=8080
```

**Numa instalação existente, mantenha o nome de projecto Compose e o ficheiro `.env` utilizados anteriormente.** Mudar o nome do projecto selecciona outro volume e pode apresentar uma base vazia. O exemplo acima não deve substituir a configuração de uma instalação que já contém dados.

Se a porta 8080 estiver ocupada, verifique se existe outra instância da aplicação em execução. Para escolher outra porta, defina `PMF_PORT=8081` no `.env`, execute `docker compose up -d --wait` e aceda a http://localhost:8081.

O serviço é publicado apenas em `127.0.0.1`, para acesso no próprio computador.

## Contas e permissões

- Utilizadores autenticados podem consultar dados e documentos de acordo com as permissões das páginas.
- Administradores podem alterar dados, gerir turmas, publicar guias e efectuar eliminações autorizadas. A cópia integral da base é reservada a superutilizadores, porque inclui respostas privadas 360°.
- A conta criada com `createsuperuser` tem acesso administrativo. Não é necessário recriá-la em cada actualização quando a mesma base é conservada.
- As permissões são verificadas no servidor.

## Importação de dados Excel

A importação utiliza o comando Django `import_excel`. O ficheiro deve respeitar uma das estruturas EPAL/PROÁGUA reconhecidas pelo importador.

Coloque o ficheiro na pasta do projecto com o nome `dados.xlsx` e copie-o para o backend:

```bash
docker compose cp dados.xlsx backend:/tmp/import.xlsx
```

Execute primeiro uma **simulação sem guardar os registos**:

```bash
docker compose exec backend python manage.py import_excel /tmp/import.xlsx --report /tmp/simulacao.json
docker compose exec backend python -m json.tool /tmp/simulacao.json
```

Reveja as alterações e os erros indicados. Crie uma cópia de segurança antes da importação efectiva, sobretudo se a base já contém dados. Para guardar os registos válidos:

```bash
docker compose exec backend python manage.py import_excel /tmp/import.xlsx --apply --report /data/resultado-importacao.json
```

Na importação integrada, o número mecanográfico identifica o técnico e o ID de participação permite reconhecer participações existentes. A reimportação conserva os resultados das participações integradas já guardadas. Dados provenientes de outro formato ou sistema de identificação exigem revisão antes da fusão.

Os caminhos `/tmp` e `/data` destes comandos pertencem ao contentor. Os ficheiros Excel de origem e os relatórios com dados pessoais devem permanecer fora do repositório público.

## Certificados e documentos privados

No histórico, os administradores encontram **Certificado e entregas** em cada participação. A emissão, a declaração do responsável, a entrega confirmada ao participante e a entrega confirmada aos RH têm campos separados. Declarações conservam origem e responsável, sem inventar datas; não aumentam o indicador de entregas confirmadas. A entrega confirmada exige a data efectiva.

O acesso **Documentos privados**, na ficha ou histórico, permite anexar CV ao trabalhador e certificados/comprovativos à participação correcta. Apenas administradores podem carregar, consultar e descarregar os PDFs. Os ficheiros ficam fora dos recursos públicos, com nomes internos UUID e verificação SHA-256. O controlo do formato é básico; o conteúdo não é convertido nem executado pelo servidor.

Arquivar preserva o ficheiro e o histórico; reactivar exige um motivo, tal como arquivar. Anexar um comprovativo não altera automaticamente os estados do certificado. Participações com documentos associados ficam protegidas contra eliminação.

## Cópias de segurança e persistência

A base fica em `/data/pmf.sqlite3` dentro do backend. O volume `database` conserva os dados quando os contentores são substituídos ou removidos sem apagar o volume.

Para exportar uma cópia, entre como administrador e escolha **Cópia de segurança**:

- Sem relatórios nem documentos privados, a aplicação fornece um ficheiro SQLite.
- Com relatórios ou documentos privados, fornece um ZIP com a base, os PDFs (incluindo documentos arquivados) e a chave local quando disponível. Conserve o ZIP completo. A exportação recusa documentos privados ausentes ou alterados.

No arranque com uma base existente, o script Docker guarda uma cópia da base, dos relatórios, dos documentos privados e da chave em `/data/backups/` antes das migrações. Estas cópias acumulam-se no mesmo volume; mantenha também uma cópia noutro suporte para protecção contra perda do disco.

Para recuperar um ZIP ou SQLite exportado pela aplicação, execute no ambiente Python do backend:

```bash
python manage.py restore_backup COPIA.zip --destination NOVA_PASTA
```

A cópia tem de estar acessível nesse ambiente; em Docker, disponibilize-a num volume ou copie-a para o contentor. A pasta principal do destino deve existir, ser gravável pelo backend, e `NOVA_PASTA` não pode existir. A recuperação verifica a integridade SQLite, as relações e as assinaturas dos PDFs; não substitui uma base em uso. O limite é 5 GB por arquivo e 1 GB por ficheiro.

Depois da validação, pare a aplicação, conserve a instalação anterior e configure `PMF_DATA_DIR`/o volume para utilizar a pasta recuperada. Os ficheiros devem continuar acessíveis ao utilizador do backend (`10001:10001`). Não elimine a cópia anterior antes de testar o arranque. Uma exportação SQL exige uma ferramenta SQLite; este comando aceita SQLite binário ou ZIP da aplicação.

**Não utilize `docker compose down -v` numa actualização normal: essa opção remove o volume de dados do projecto.**

## Actualização

Crie uma cópia de segurança, actualize o código e execute na pasta do projecto:

```bash
docker compose build
docker compose up -d --wait
```

Não é necessário executar `down` antes da construção. O comando `up` substitui os contentores que precisam de actualização. Mantenha o mesmo `.env` e o mesmo nome de projecto Compose para conservar a ligação à base existente.

Depois do arranque, actualize o navegador com **Ctrl+F5**. Não volte a importar o Excel para aplicar uma actualização de código: as migrações adaptam a base existente.

## Comandos úteis

```bash
# Consultar o estado dos contentores
docker compose ps

# Consultar as últimas mensagens dos serviços
docker compose logs --tail=100 backend frontend

# Parar e voltar a iniciar os mesmos contentores
docker compose stop
docker compose start

# Remover os contentores, conservando o volume de dados
docker compose down

# Abrir a consola Django
docker compose exec backend python manage.py shell
```

## Testes

Para executar os testes com as dependências da imagem backend, sem utilizar a base activa:

```bash
docker compose run --rm --no-deps -e PMF_DATA_DIR=/tmp/pmf-tests backend python manage.py test --noinput
```

Os testes abrangem regras de dados, permissões, revisão colectiva, planeamento, exportações, relatórios, assistente e eliminações protegidas. O teste opcional de leitura estrutural de PDF é ignorado quando `pypdf` não está disponível.

## Estrutura do repositório

```text
pmfe/
├── app/
│   ├── pmf/                 # Configuração Django e rotas
│   ├── training/            # Modelos, lógica, migrações e testes
│   │   └── resources/       # Modelo Excel de listas de presença
│   ├── templates/           # Páginas HTML
│   ├── static/              # CSS, JavaScript, fontes e imagens
│   ├── manage.py
│   └── requirements.txt
├── docker/
│   ├── nginx.conf
│   └── startup.py
├── backend.Dockerfile
├── frontend.Dockerfile
├── compose.yaml
├── .dockerignore
├── .gitignore
└── README.md
```

O `.gitignore` exclui ambientes locais, `.env`, segredos, bases, cópias de segurança, exportações SQL e ficheiros Excel com dados pessoais. O modelo `app/training/resources/attendance_template.xlsx` é conservado porque a aplicação depende dele.

Entre os documentos Markdown, apenas o `README.md` da raiz está previsto para publicação. Os restantes ficheiros `.md` ficam excluídos pelo `.gitignore`.

## Limitações actuais

- A configuração destina-se a um único computador e a uma única instância backend com SQLite.
- A avaliação 360°, o plano de desenvolvimento e os indicadores confirmados estão disponíveis. A recolha 360° é feita através de contas individuais autorizadas, no computador local. O arquivo privado de CV continua disponível para administradores.
- O assistente utiliza guias locais; não é uma IA generativa nem aprende automaticamente novas funcionalidades.
- Existem listas diárias de presença, mas o cálculo de horas utiliza os totais de dias de presença registados.
- A plataforma acompanha a entrega de certificados; a emissão automática a partir de um modelo ainda não está implementada.
- Os indicadores dependem da qualidade dos registos. Os dados EPAL não validados pelos RH continuam provisórios.


## Avaliação da formação, provas e reacção

O menu **Avaliação**, disponível aos administradores, permite pesquisar uma participação existente e registar:

- **Avaliação da formação:** logística numa escala explícita de 1 a 5, nota final de 0 a 20 e resultado final independentes. Valores desconhecidos permanecem vazios; concluir a formação não atribui aprovação.
- **Reacção:** organização, conteúdo, formador e utilidade de 1 a 5, com comentários. É possível guardar uma resposta parcial, conservando vazios os itens não respondidos.
- **Provas:** tipo diagnóstica/intermédia/final, questões de resposta textual com critérios de correcção manual, referência do manual e cotação positiva. O limiar de aprovação é configurável entre 0% e 100%; o formulário propõe 50% e o responsável deve confirmar o critério aplicável.
- **Tentativas e respostas:** um administrador inicia a primeira tentativa. As seguintes exigem a conclusão da anterior e um motivo de autorização. Todas as tentativas permanecem no histórico.

Para preparar uma prova, guarde o título e as questões. Cada gravação apresenta mais três espaços para novas questões, até ao limite de 100. Após iniciar a primeira tentativa, as questões e o critério ficam fechados; crie uma nova prova para uma versão diferente.

Na correcção, registe as respostas e os pontos atribuídos. Uma pontuação vazia significa **não classificada**; zero é uma classificação explícita. Pode guardar um rascunho. **Concluir correcção** exige todas as pontuações, data, responsável e fonte documental, e conserva o resultado. A percentagem é `pontos obtidos / cotação total × 100`; a nota /20 é a percentagem dividida por cinco. A aprovação compara a percentagem sem arredondamento com o limiar registado; os ecrãs apresentam duas casas decimais.

O resultado de uma tentativa **não altera automaticamente** a nota final da participação. O responsável regista essa decisão em **Avaliação e nota final**, com motivo e fonte. Isto permite distinguir provas diagnósticas, reavaliações e uma classificação final baseada em vários elementos.

Os registos incluem fonte documental, data, responsável e estado **Por confirmar / Validado / Rever**. Marcar como validado exige data, responsável e fonte; não significa que a aplicação tenha verificado o documento. Correcções exigem motivo, conservam o antes/depois na auditoria e rejeitam gravações de uma janela desactualizada. Uma tentativa concluída é conservada; uma nova avaliação exige outra tentativa autorizada.

Os registos são privados aos administradores; não existe portal de realização de provas pelos participantes nem correcção automática de respostas. As relações com a participação evitam duplicar o cadastro do trabalhador. A cópia de segurança e a recuperação da base incluem avaliações, questões, tentativas e respostas. A importação automática de folhas Excel continua a exigir uma fase própria. O módulo 360° é descrito abaixo.


## Competências e avaliação 360°

1. Em **Avaliação → Gerir ciclos e competências → Catálogo e funções**, crie competências e funções de referência. Cada regra define nível esperado (1–5), peso relativo da competência e validação RH com responsável e data. Pode desactivar entradas; os ciclos antigos conservam as suas regras.
2. Crie um ciclo para um trabalhador. A função do catálogo é escolhida explicitamente: o texto `Função` do cadastro não é substituído nem convertido automaticamente. Pode associar a participação do mesmo trabalhador ou uma turma com inscrição activa.
3. Defina momento, período, pesos dos tipos de avaliador, cobertura mínima por tipo e mínimo de colegas. Os pesos propostos são 10% autoavaliação, 40% superior, 25% colegas e 25% formador; devem somar 100% e são confirmados pelo responsável ao abrir. Tipos com peso zero não recebem avaliadores autorizados.
4. Autorize contas individuais e confirme a identidade e o tipo de cada avaliador. Cada conta participa uma única vez por ciclo; só existe uma autoavaliação. A aplicação não deduz a identidade de uma conta a partir do nome do trabalhador.
5. Abra o ciclo. A abertura conserva uma cópia dos nomes, descrições, níveis esperados, pesos, validações RH e configuração. Alterar o catálogo posteriormente não muda os resultados históricos. A configuração, os avaliadores e os prazos ficam fixos.
6. Cada avaliador entra com a própria conta e utiliza **Minhas avaliações**. Pode guardar e corrigir rascunhos dentro do período. Entregar exige nota 1–5 em todas as competências; a entrega conserva a resposta. Não são enviados convites por correio electrónico.
7. O responsável fecha o ciclo. Não há médias em tempo real, para evitar inferências a partir de entregas sucessivas. Fechar ou cancelar é definitivo; uma nova recolha exige outro ciclo.

Para criar uma conta de leitura/avaliador sem privilégios administrativos:

```bash
docker compose exec backend python manage.py create_evaluator nome_do_avaliador
```

O comando pede e confirma a palavra-passe sem a mostrar e aplica os validadores de palavras-passe. Não coloque palavras-passe em comandos, ficheiros ou Git. A conta mantém as permissões de consulta existentes na plataforma e só responde aos ciclos em que foi autorizada. O comando não autoriza avaliações nem concede acesso administrativo.

### Cálculo e confidencialidade

Para cada competência, calcula-se a média das respostas entregues de cada tipo de avaliador e aplica-se o peso percentual desse tipo. O índice do ciclo é a média desses resultados ponderada pelos pesos relativos das competências. Não se redistribui o peso de grupos em falta.

Cada tipo com peso positivo deve atingir a cobertura configurada, calculada como respostas entregues divididas pelos avaliadores autorizados desse tipo. Colegas exigem ainda **pelo menos três respostas distintas**, mesmo que a cobertura percentual já tenha sido atingida. Se faltar cobertura ou o mínimo de colegas, o resultado fica indisponível; respostas em rascunho não contam e ausência de resposta não equivale a zero.

O responsável do ciclo e os superutilizadores consultam apenas resultados agregados nas páginas de gestão. As respostas e comentários individuais, bem como o histórico das suas correcções, são acessíveis na interface apenas à conta que os registou. A auditoria geral regista a operação de resposta, sem copiar notas, comentários ou motivos privados. Os comentários não são publicados nos agregados. Outros administradores não consultam ciclos que não gerem.

A confidencialidade da interface não oculta a base ao administrador do computador: o SQLite e a cópia integral contêm as respostas e o seu histórico. Por isso, **Cópia de segurança** permanece disponível apenas para superutilizadores. A recuperação conserva esses dados, as autorizações e a configuração histórica dos ciclos.

## Plano de desenvolvimento

Um ciclo fechado com resultados divulgáveis mostra a lacuna por competência: `máximo(0, nível esperado − resultado)`. A partir de uma lacuna positiva, utilize **Planear desenvolvimento** para registar acção, responsável pela execução, prazo e, opcionalmente, formação recomendada do catálogo.

Em **Desenvolvimento**, acompanhe os estados Planeada, Em curso, Concluída e Cancelada, incluindo prazos ultrapassados. Concluir ou cancelar exige data e evidência/comentário de revisão. As correcções são auditadas e gravações de uma janela desactualizada são rejeitadas. Concluir uma acção não modifica a pontuação histórica; um novo ciclo permite medir a evolução. As formações recomendadas não geram inscrições automaticamente.

## Indicadores confirmados

O menu **Indicadores confirmados** e a ligação no dashboard separam cinco famílias de informação, com filtros de trabalhador e período, denominadores e conciliação por participação:

| Família | Regra de inclusão |
| --- | --- |
| Presença | Formação concluída, presenças verificadas e dias conhecidos; soma de presenças dividida pela soma dos dias |
| Aprendizagem | Nota final presente numa participação concluída, com avaliação validada que ainda corresponde exactamente à nota e ao resultado actuais |
| Logística e satisfação | Avaliações/reacções validadas; cada dimensão conta apenas os valores respondidos, com denominador próprio |
| Competências | Ciclos fechados, autorizados ao utilizador e com cobertura suficiente; média dos índices históricos por ciclo |
| Certificados | Emissão, entrega ao participante, entrega aos RH e declarações separados; valores desconhecidos apresentados à parte |

O filtro temporal usa a data de início da participação ou do ciclo. Não é um relatório de entregas ocorridas no período. A média de competências é por ciclo, não por trabalhador único. Uma declaração ou um ficheiro PDF não confirma automaticamente uma entrega.

Os totais são derivados das relações existentes, sem tabelas duplicadas para reproduzir folhas de resumo. Os indicadores mostram os registos excluídos/provisórios e permitem conciliá-los com a participação ou ciclo de origem. Os relatórios executivos anteriores conservam a análise histórica e os seus avisos de dados provisórios; não são apresentados como exportação dos novos indicadores confirmados.

Após a migração, avaliações antigas conservam as notas e fontes, mas não recebem uma confirmação de nota inventada. Para incluí-las na aprendizagem confirmada, reveja e guarde a avaliação com estado **Validado**. Se a nota ou o resultado mudar depois, é necessária nova validação. A importação real de dados e a aceitação visual/manual no Windows permanecem fases posteriores.


### Avaliação — mapa de competências e percurso do formando

O mapa encontra-se dentro de **Avaliação**, sem entrada 360° independente na navegação principal. Seleccione um formando para abrir o **Centro de Avaliação de Desempenho**: radar de competências, pontos fortes, áreas a desenvolver, tabela de evolução, participações e momentos de avaliação. A avaliação de uma participação também permite abrir este perfil.

O radar utiliza a apresentação original do centro de avaliação e os resultados reais dos ciclos autorizados ao utilizador. Verde representa o ciclo seleccionado, azul uma avaliação anterior compatível e o tracejado o nível esperado, sempre na escala explícita /5. **Visão 360°** percorre as competências; pode parar a exploração ou seleccionar uma dimensão directamente. A tabela permanece disponível sem JavaScript. Com movimento reduzido, o avanço faz-se manualmente.

A comparação só inclui ciclos anteriores fechados com a mesma função de referência, competências, descrições, níveis esperados e pesos. Valores ausentes ou não divulgáveis não se tornam zero; não se geram notas demonstrativas. Com menos de três competências, apresentam-se os valores em tabela, sem inventar eixos para completar o radar. O percurso de formação e as notas /20 permanecem separados das competências /5.


## Administração e primeiros registos

Abra **Administração** no menu ou aceda a `http://localhost:8080/admin/`. Utilize uma conta activa com os atributos de membro da equipa e superutilizador. Uma conta administrativa já existente pode entrar directamente.

O painel apresenta contagens e uma sequência de preparação:

1. **Contas e acessos**: crie contas individuais e, depois, configure o nível de acesso. Conta normal permite consulta e avaliações atribuídas; membro da equipa permite gestão operacional; superutilizador permite administração de contas e cópia integral.
2. **Trabalhadores**: registe o cadastro dos formandos.
3. **Catálogo de formação**: prepare as acções antes das turmas e inscrições em Planeamento.
4. **Competências**: prepare as dimensões de avaliação.
5. **Funções e regras RH**: associe competências, níveis esperados e pesos às funções.

As listas administrativas permitem pesquisar os cadastros. Adicionar ou alterar um cadastro abre o formulário operacional existente, conservando as suas validações e auditoria. A gestão de contas utiliza os formulários Django, com palavras-passe protegidas. Desactive contas que deixem de ser usadas; a eliminação está desabilitada para preservar o histórico. O administrador não pode retirar os próprios privilégios nesta página.

Respostas individuais de avaliação, provas e documentos privados não são expostos como tabelas editáveis no painel administrativo.

Apenas se ainda não existir um superutilizador, crie o primeiro pelo terminal:

```bash
docker compose exec backend python manage.py createsuperuser
```

O build Docker recolhe automaticamente os ficheiros estáticos do painel. Para execução directa fora do Docker, execute `python manage.py collectstatic --noinput` no directório que contém `manage.py`.

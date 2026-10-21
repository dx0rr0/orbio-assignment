# Cases of the time-to-first-audio eval

20 single-turn cases: the 16 recordings scripted in [../recordings.md](../recordings.md) plus four clips of the e2e fixtures. The source of truth is [cases.py](cases.py); this table is for review.

**Split:** the hill-climb rounds run on 13 cases (`--split train`); 1b, 3a, R03, R07, R10, R12 and R16 (one of each kind) are held out as `test` (`TEST_IDS` in cases.py) and only validate the result.

**Repetitions:** `--reps` defaults to 1, but the rounds run with `--reps 3`: one sample per case is too noisy to compare variants. The pilot measured about $0.00036 per case, so a train round costs about 13 × 3 × $0.00036 ≈ $0.014.

Grading is programmatic, no LLM judge: each case has checks on the claim after the turn (and, for R11, on what the agent said). `pass` is 1 when every check holds; `checks` is the fraction that hold. Times are checked with a tolerance (±45 min unless noted) and days relative to the day the case runs.

| id | tags | starts from | the agent just said | utterance | checks |
|---|---|---|---|---|---|
| 1a | identification | empty claim | greeting | "Hola, buenas. Me llamo María López Fernández. Mi número de póliza es AUT48213907 y la matrícula es 4721 KLM." | policy AUT-48213907, plate 4721KLM, name has "María" |
| 1b | loss | María identified | "¿Conducía usted el vehículo? ¿Qué ocurrió y cuándo?" | "Conducía yo, ayer por la tarde sobre las 7, iba por la A3 a la altura de Arganda del Rey, en Madrid, y una piedra… me rompió el parabrisas. No hay heridos y el coche se puede mover." | policyholder driving, broken glass, starts yesterday 19:00 (±90 min), Arganda del Rey / Madrid, no injuries, not immobilized, pending confirmation (the read-back follows) |
| 3a | identification | empty claim | greeting | "Hola, me han robado el coche. Soy Carlos Moreno Díaz. La póliza es AUT 73058416 y la matrícula es 6614 LPR." | policy AUT-73058416, plate 6614LPR, name has "Carlos", theft |
| 3b | loss | Carlos identified, theft, driving | "¿Cuándo y dónde vio el coche por última vez…?" | "Lo dejé aparcado el martes de la semana pasada por la noche en la calle Mayor de Alcalá de Henares, y a la mañana siguiente ya no estaba. Ya he puesto la denuncia." | window from last week's Tuesday to Wednesday, Alcalá de Henares / Madrid, police report filed, closed as not covered (policy started 3 days ago) |
| R01 | identification, spelled | empty claim | greeting | Lucía, policy and plate spelled digit by digit | policy AUT-62947105, plate 2196FDS, name has "Lucía" |
| R02 | identification, spelled | empty claim | "¿Me puede dar su número de póliza?" | policy number in groups | policy AUT-48213907 |
| R03 | identification, spelled | policy known | "¿Y la matrícula del vehículo?" | plate with letters as words | plate 4721KLM |
| R04 | identification, short | policy and plate | "¿Me dice su nombre completo?" | "Me llamo María López Fernández." | name exactly "María López Fernández" |
| R05 | driver, short | María identified | "¿Conducía usted el vehículo…?" | "Sí." | policyholder driving; no driver name, loss type, window, location, description, injuries, immobilized, other vehicle or police report invented |
| R06 | driver | María identified | "¿Conducía usted el vehículo…?" | "No, conducía mi hijo, Pablo López." | not the policyholder, driver name has "Pablo" |
| R07 | loss | María, driving | "¿Qué ocurrió y cuándo?" | broken glass, "anteayer… sobre las diez y media" | broken glass, starts two days ago 10:30 |
| R08 | location | broken glass, window known | "¿Dónde ocurrió?" | "En Valencia, en la avenida del Puerto." | Valencia / Valencia, way has "Puerto" |
| R09 | location | same | "¿Dónde ocurrió?" | AP-7, km 340, near Castellón | province Castellón, way has "AP-7" |
| R10 | location, short | same, after "En Valencia, en la avenida del Puerto." | "¿Recuerda el número de la avenida del Puerto?" | "Ocho." | Valencia / Valencia, way has "8" |
| R11 | damage | everything but injuries and immobilized | "¿Hubo algún herido?" | "Sí, mi mujer se ha hecho daño en el cuello, y el coche no arranca." | injuries, immobilized, the agent said "112" |
| R12 | confirmation | complete claim (yesterday 19:00) | the read-back | "No, la hora no está bien: fue a las ocho y media, no a las siete." | starts yesterday 20:30 (±20 min), still pending confirmation |
| R13 | off_topic | broken glass, window known | "¿Dónde ocurrió?" | "¿esto me va a subir la prima del seguro?" | no location, description or injuries invented; still in progress |
| R14 | driver, refusal | María, not the policyholder | "¿Cómo se llama la persona que conducía?" | "Prefiero no dar el nombre de mi hijo." | no driver name invented; still in progress |
| R15 | other_vehicle, spelled | collision with another vehicle | "¿Cuál es la matrícula del otro vehículo?" | hesitation, then "dos cinco cuatro ocho, eme, ene, pe" | other plate 2548MNP |
| R16 | loss | María, driving | "¿Qué ocurrió?" | parked Friday night in calle Alcalá, Madrid; gone Saturday morning; no police report yet | theft, window last Friday to Saturday, Madrid / Madrid, way has "Alcalá", no police report |

## Recorded per case

- **TTFA** (`ttfa_s`): seconds from sending the audio to the first `audio` event, measured on the WebSocket.
- **Breakdown:** `stt_s` (until the transcript event), `engine_s` (transcript → first bot message), `tts_first_s` (first bot message → first audio).
- **End of a case:** once the claim update and the first audio are out. Waiting for silence instead would cancel the consumer (an asgiref receive timeout does that).
- **LLM:** number of calls, how many failed, and total LLM seconds (failed calls included). Tokens are not recorded: the chat model port does not return usage; cost comes from the key (below).
- **Cost:** per run only, in `summary.json` (total and per case): the key's usage lags a few seconds behind the calls, so it can't be split per case.
- **Warm-up:** one unrecorded R05 turn runs before the first case, so the cold start (TLS handshakes, the first LLM call) is not charged to whichever case comes first. It is paid for, and counted in the cost per case.
- **Infrastructure errors** stop the run and go to `errors.jsonl` instead of being scored: a failed transcription or synthesis (with the provider's reason, as soon as the voice reports it, instead of waiting for audio until the case timeout), an `error` event, or a reply with the technical-problem template, which the engine sends after LLM failures (the failed calls are recorded with it).
- While the TTS isn't streamed, `tts_first_s` is the whole synthesis: the first audio is the whole reply.

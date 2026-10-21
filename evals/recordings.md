# Recording scripts for the latency eval

Declarant utterances for the time-to-first-audio eval. Each one is a single turn: the context says what the agent has just asked, so the eval can rebuild the claim state that turn starts from. Together with the nine clips in `tests/e2e/fixtures/audio/`, they cover the speech patterns that matter for latency and extraction: spelled-out identifiers, relative dates, ambiguous places, very short answers, corrections and off-topic turns.

## How to record

- Any recorder works (Windows "Grabadora de sonido" is fine). Files get converted to 16 kHz mono WAV with `ffmpeg`.
- Quiet room, normal conversational speed, as if on the phone. Hesitations are welcome where the script has them.
- Say the script, not a paraphrase: the expected values are written against it.
- Name each file after its id (`R01.m4a`, `R02.m4a`…) and leave them in `evals/audio/raw/`.

## Scripts

| id | context (the agent just asked…) | say | what it tests |
|---|---|---|---|
| R01 | greeting: policy number and plate | "Hola, soy Lucía Navarro. Mi póliza es A, U, T, seis, dos, nueve, cuatro, siete, uno, cero, cinco, y la matrícula dos uno nueve seis, efe, de, ese." | identifiers spelled out one by one |
| R02 | policy number | "La póliza es la AUT cuatro ocho dos uno, tres nueve cero siete." | policy number in groups |
| R03 | plate (policy AUT-48213907 already known) | "La matrícula es cuatro siete dos uno, ka de kilo, ele de Lérida, eme de Madrid." | letters spelled with words |
| R04 | declarant's name | "Me llamo María López Fernández." | short, clean answer |
| R05 | whether the policyholder was driving | "Sí." | one-word answer |
| R06 | whether the policyholder was driving | "No, conducía mi hijo, Pablo López." | conditional field in the same answer |
| R07 | what happened and when | "Fue una rotura de lunas, anteayer por la mañana, sobre las diez y media." | loss type plus relative date |
| R08 | where it happened | "En Valencia, en la avenida del Puerto." | city that is also a province |
| R09 | where it happened | "En la AP-7, a la altura del kilómetro trescientos cuarenta, cerca de Castellón." | road and kilometre point instead of a street |
| R10 | street number of the location | "Ocho." | one-character transcription |
| R11 | whether anyone was hurt | "Sí, mi mujer se ha hecho daño en el cuello, y el coche no arranca." | injuries (112 advice) and immobilized vehicle together |
| R12 | read-back of the claim | "No, la hora no está bien: fue a las ocho y media, no a las siete." | correction during confirmation |
| R13 | where it happened | "Oiga, una pregunta: ¿esto me va a subir la prima del seguro?" | off-topic question mid-interview |
| R14 | the driver's name | "Prefiero no dar el nombre de mi hijo." | refusing a required field |
| R15 | the other vehicle's plate | "Eh… a ver… déjeme mirar… vale, es dos cinco cuatro ocho, eme, ene, pe." | hesitation before the data |
| R16 | what happened | "Lo dejé aparcado el viernes por la noche delante de casa, en la calle Alcalá de Madrid, y el sábado por la mañana ya no estaba. Todavía no he ido a la policía." | long answer with a loss window and several fields |

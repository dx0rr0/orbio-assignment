# Auto Claim Intake

Voice agent that talks to the declarant on behalf of a fictional car insurer and collects the first notice of a loss. The agent speaks Spanish; the Spanish term for each concept is given in parentheses.

## Language

### The claim

**Loss** (*Siniestro*):
The event that happened (collision, theft, broken glass…) that may be covered by a car insurance policy.
_Avoid_: Incident, accident (as a generic term), claim

**Claim** (*Declaración de siniestro*):
The data about a loss that the agent collects from the declarant during a conversation.
_Avoid_: Report, ticket, case, FNOL (except when describing the industry practice)

**Loss window** (*Momento del siniestro*):
The interval in which the loss happened, from `from` to `to`. It collapses to an instant when the declarant knows when it happened. For a theft or parked-car damage it runs from when the vehicle was last seen to when the damage was discovered.
_Avoid_: Date, time, timestamp

**Loss location** (*Lugar del siniestro*):
Normalized location of the loss: optional way (street, road, kilometre point), municipality, province and country. Only losses in Spain are accepted.
_Avoid_: Address

**Province** (*Provincia*):
One of Spain's 52 provinces (Ceuta and Melilla included). It is the "region" level of a loss location.
_Avoid_: Region, autonomous community

**Urgency** (*Urgencia*):
Priority of a claim (`high`, `medium`, `low`). It is derived from the claim's data (injuries, immobilized vehicle) and never asked.
_Avoid_: Priority, severity

### People and policy

**Policy** (*Póliza*):
Car insurance contract covering one vehicle, identified by its plate, for a coverage period.
_Avoid_: Contract, insurance (as a countable noun)

**Coverage period** (*Vigencia*):
The span between a policy's start and end dates. A loss is only declarable when its whole loss window falls inside it.

**Policyholder** (*Titular*):
The person the policy is in the name of.
_Avoid_: Customer, insured (as a synonym)

**Declarant** (*Declarante*):
The person talking to the agent to declare the loss. May be someone other than the policyholder.
_Avoid_: User, caller, customer

**Driver** (*Conductor*):
The person driving the insured vehicle when the loss happened. May be neither the policyholder nor the declarant.

### Lifecycle

**Confirmation** (*Confirmación*):
The moment the declarant approves the read-back of the complete claim. After it the claim is closed.

**Claim status** (*Estado de la declaración*):
`in_progress` → `pending_confirmation` → `confirmed`, or one of the other final statuses `incomplete`, `not_covered` or `escalated`. Only code moves a claim between statuses, never the LLM.

**Escalation** (*Derivación*):
Handing a claim over to a human adjuster because the agent can't or shouldn't resolve it (retries exhausted, a loss window that is only partly covered, persistent errors).
_Avoid_: Transfer, handoff

**Closure reason** (*Motivo de cierre*):
Why a claim ended in a state other than confirmed (data refused, abandonment, retries exhausted, system error, outside coverage period, only partly inside the coverage period, outside Spain).

**Pending fields** (*Campos pendientes*):
The required fields a non-confirmed claim lacks when it closes, each with its last validation error, if any.

# GURPS law, legality, and enforcement

The engine records a jurisdiction's Control Rating separately from each catalog
definition's Legality Class (Campaigns B506-B507). Availability is resolved from
that pair: LC above CR is open, equality may require registration, and successive
steps below CR require a license or become restricted to authorities. A permit is
an explicit authored fact tied to jurisdiction, item definition, holder, and
scope; it never changes the catalog item.

Crimes select authored evidence fact IDs. A case records which authorities know
each fact without teaching it to every character. Missing world truth is rejected,
so an enforcement or social failure cannot manufacture evidence.

Travel etiquette, enforcement encounters, arrest, jail, trial, bribery, and
punishment are authored state transitions. A procedure chooses the existing
success, reaction, or Quick Contest scorer, or an explicit automatic transition.
Timed procedures advance the shared campaign clock. Stable command receipts and
case procedure IDs prevent duplicate settlement; punishment retains its authored
resource/effect dispatch string for the owning consequence service.

The current family deliberately rejects unregistered procedure kinds, trial
models, jurisdictions, items, crimes, and permits. API and UI exposure remain out
of scope for #502.

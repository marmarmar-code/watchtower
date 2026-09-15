# Public and private source separation


## Reviewed public topic vocabulary

The private runtime may declare `privacy.public_topic_terms` for ordinary topic words
or official source vocabulary that has been checked against the public implementation.
This prevents those words in filters and search queries from being mistaken for
installation-specific information. The default remains strict when the list is absent.

Review each term before adding it. Do not use this for private names, affiliations,
watchlists or identifiers. Explicit `privacy.protected_values`, entity names/aliases
and configured registry identifiers cannot be exempted; overlap causes validation
to fail. Keep the approved vocabulary and its rationale in the private runtime.

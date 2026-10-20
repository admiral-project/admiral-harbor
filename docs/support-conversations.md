# Customer support conversations

Customers and Harbor staff can exchange public replies on a support ticket.
Harbor staff use the public reply form in the ticket detail page; customers
see those replies in their portal conversation and can reply there.

Staff-only notes remain in the separate internal notes field and are never
included in the customer conversation. Public staff replies are stored in the
`staff_reply` table by the Harbor schema migration and appear in the audit log
without copying message text into the audit detail.

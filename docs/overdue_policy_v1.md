# Harbor overdue and cancellation policy v1

This document defines the billing behavior for a paid Harbor subscription.

## Provisioning failure

If the first provisioning attempt fails and the instance reaches
`setup_failed`, Harbor must:

1. cancel the PayPal subscription; and
2. issue a full refund for the captured payment.

Harbor must not keep a payment for an instance that could not be initialized.

## Customer cancellation

When a customer cancels an active subscription:

- Harbor cancels the PayPal subscription so future billing periods are not
  charged.
- Harbor does not issue a partial or prorated refund.
- The instance remains available through the end of the prepaid period.
- After the prepaid period ends, the worker queues deprovisioning.

The cancellation state is recorded in Harbor while the technical instance may
remain running during the prepaid period.

## Overdue payment

Overdue payment handling remains separate from voluntary cancellation. The
configured overdue policy may pause or deprovision an unpaid service according
to its configured grace periods; it does not create a refund entitlement.

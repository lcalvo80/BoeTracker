import React from "react";
import { BuyProButton, EnterpriseCheckoutButton } from "../components/billing/BillingButtons";

export default function Pricing() {
  return (
    <div className="mx-auto max-w-5xl px-4 py-10 grid md:grid-cols-3 gap-6">
      <div className="border rounded p-6">
        <h3 className="text-xl font-semibold">Free</h3>
        <p className="mt-2 text-sm opacity-70">Para probar y promociones puntuales.</p>
        <ul className="mt-4 text-sm space-y-1">
          <li>✓ Feature A</li>
          <li>✗ Feature B</li>
          <li>✗ Export CSV</li>
        </ul>
        <button className="mt-6 px-4 py-2 rounded border" disabled>Actual</button>
      </div>

      <div className="border rounded p-6">
        <h3 className="text-xl font-semibold">Pro</h3>
        <p className="mt-2 text-sm opacity-70">Acceso completo individual.</p>
        <ul className="mt-4 text-sm space-y-1">
          <li>✓ Feature A</li>
          <li>✓ Feature B</li>
          <li>✓ Export CSV</li>
        </ul>
        <div className="mt-6"><BuyProButton /></div>
      </div>

      <div className="border rounded p-6">
        <h3 className="text-xl font-semibold">Enterprise</h3>
        <p className="mt-2 text-sm opacity-70">Por organización, precio por asiento.</p>
        <ul className="mt-4 text-sm space-y-1">
          <li>✓ Todo Pro</li>
          <li>✓ SSO & roles</li>
          <li>✓ Seat billing</li>
        </ul>
        <div className="mt-6"><EnterpriseCheckoutButton seats={5} /></div>
      </div>
    </div>
  );
}

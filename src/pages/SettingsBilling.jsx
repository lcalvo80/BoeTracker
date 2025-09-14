import React from "react";
import { useUser, useOrganization } from "@clerk/clerk-react";
import { BillingPortalButton } from "../components/billing/BillingButtons";

export default function SettingsBilling() {
  const { user } = useUser();
  const { organization } = useOrganization();

  const userPlan = user?.publicMetadata?.plan || "free";
  const orgPlan = organization?.publicMetadata?.plan || "—";

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 space-y-6">
      <h2 className="text-2xl font-semibold">Billing</h2>

      <div className="border rounded p-4">
        <h3 className="font-semibold">Tu plan (usuario)</h3>
        <p className="opacity-80 text-sm">Plan actual: <strong>{userPlan}</strong></p>
      </div>

      <div className="border rounded p-4">
        <h3 className="font-semibold">Plan de la organización activa</h3>
        <p className="opacity-80 text-sm">Plan: <strong>{orgPlan}</strong></p>
      </div>

      <div className="pt-2">
        <BillingPortalButton />
      </div>
    </div>
  );
}

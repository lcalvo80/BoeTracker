import React from "react";
import { useUser, useOrganization } from "@clerk/clerk-react";
import { BuyProButton, EnterpriseCheckoutButton } from "./BillingButtons";

export function Paywall({ need = "pro", children }) {
  const { user, isLoaded } = useUser();
  const { organization } = useOrganization();

  // Fuente del plan:
  const userPlan = (user?.publicMetadata?.plan || "free");
  const orgPlan  = (organization?.publicMetadata?.plan || null);

  if (!isLoaded) return null;

  // Para features Enterprise, prioriza org
  if (need === "enterprise") {
    if (orgPlan !== "enterprise") {
      return (
        <div className="p-6 border rounded space-y-3">
          <h3 className="text-lg font-semibold">Función Enterprise</h3>
          <p className="opacity-70 text-sm">Disponible solo en organizaciones con plan Enterprise.</p>
          <EnterpriseCheckoutButton seats={5} />
        </div>
      );
    }
    return <>{children}</>;
  }

  // Para Pro
  if (need === "pro" && userPlan === "free") {
    return (
      <div className="p-6 border rounded space-y-3">
        <h3 className="text-lg font-semibold">Función Pro</h3>
        <p className="opacity-70 text-sm">Actualiza a Pro para usar esta funcionalidad.</p>
        <BuyProButton />
      </div>
    );
  }

  return <>{children}</>;
}

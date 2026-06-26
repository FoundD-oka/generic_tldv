import { NextResponse } from "next/server";
import {
  getSharedDashboardEmail,
  getSharedDashboardName,
  handleDirectLogin,
  isSharedDashboardAuthEnabled,
} from "@/lib/direct-login";

export async function POST() {
  if (!isSharedDashboardAuthEnabled()) {
    return NextResponse.json(
      { error: "Shared dashboard auth is disabled" },
      { status: 404 }
    );
  }

  const email = getSharedDashboardEmail();
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(email)) {
    return NextResponse.json(
      { error: "DASHBOARD_SHARED_AUTH_EMAIL must be a valid email address" },
      { status: 500 }
    );
  }

  return handleDirectLogin(email, {
    name: getSharedDashboardName(),
    bypassRegistrationPolicy: true,
    mode: "shared",
  });
}

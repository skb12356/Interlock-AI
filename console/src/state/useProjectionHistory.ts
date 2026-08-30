import { useEffect, useReducer, useState } from "react";

import { ProjectionConnection, type ProjectionStatus } from "../api/projectionClient";
import { consoleReducer, initialConsoleState } from "./consoleStore";

/** Owns the projection socket and keeps its replayed history in the pure event store. */
export function useProjectionHistory() {
  const [state, dispatch] = useReducer(consoleReducer, initialConsoleState);
  const [status, setStatus] = useState<ProjectionStatus>("stopped");

  useEffect(() => {
    if (typeof WebSocket === "undefined") return;
    const connection = new ProjectionConnection({
      onEnvelope: (envelope) => dispatch({ type: "projection.received", envelope }),
      onStatus: setStatus,
      onDiagnostic: (message) => dispatch({
        type: "diagnostic.received",
        code: "projection-transport",
        message,
      }),
    });
    connection.start();
    return () => connection.stop();
  }, []);

  return { state, status };
}

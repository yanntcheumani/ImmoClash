import { io, Socket } from "socket.io-client";
import { SOCKET_URL } from "./runtime";

const socketUrl = SOCKET_URL;

export const socket: Socket = io(socketUrl, {
  path: "/socket.io",
  autoConnect: false,
  transports: ["websocket"],
});

export async function ensureSocketConnected(): Promise<void> {
  if (socket.connected) {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const onConnect = () => {
      cleanup();
      resolve();
    };
    const onError = (error: Error) => {
      cleanup();
      reject(error);
    };
    const cleanup = () => {
      socket.off("connect", onConnect);
      socket.off("connect_error", onError);
    };

    socket.on("connect", onConnect);
    socket.on("connect_error", onError);
    socket.connect();
  });
}

export async function emitAck<T>(event: string, payload: unknown): Promise<T> {
  return await new Promise<T>((resolve, reject) => {
    socket.timeout(7000).emit(event, payload, (err: unknown, response: T) => {
      if (err) {
        reject(new Error("Socket timeout"));
        return;
      }
      resolve(response);
    });
  });
}

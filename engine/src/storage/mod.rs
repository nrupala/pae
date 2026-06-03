//! Data persistence layer for PAE.
//!
//! Holdings, portfolios, and accounts are persisted to an encrypted local
//! SQLite database (WAL journal mode, crash-recoverable) so portfolio state
//! survives between sessions. Every sensitive field is stored as a
//! client-side AES-256-GCM ciphertext blob; the engine never sees plaintext.
//!
//! See [`db`] for the concrete implementation.

pub mod db;

pub use db::{
    Account, AccountType, Holding, NewHolding, Portfolio, Store, StorageError,
};

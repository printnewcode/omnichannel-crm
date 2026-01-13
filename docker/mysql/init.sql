-- Инициализация MySQL базы данных для Omnichannel CRM
-- Этот скрипт выполняется при первом запуске контейнера MySQL
-- База данных уже создана через переменную окружения MYSQL_DATABASE

-- Получение имени базы данных из переменной окружения (по умолчанию omnichannel_crm)
-- Если база не существует, создаем ее
SET @db_name = IFNULL(@@character_set_database, 'omnichannel_crm');

-- Создание базы данных с правильной кодировкой (если еще не создана)
CREATE DATABASE IF NOT EXISTS `omnichannel_crm` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Использование созданной базы
USE `omnichannel_crm`;

-- Предоставление всех прав пользователю (если еще не предоставлены)
-- Пользователь уже создан через MYSQL_USER и MYSQL_PASSWORD, но нужно дать права
GRANT ALL PRIVILEGES ON `omnichannel_crm`.* TO 'crm_user'@'%';
FLUSH PRIVILEGES;

-- Оптимизация для высоконагруженных записей
SET GLOBAL innodb_buffer_pool_size = 1073741824; -- 1GB
SET GLOBAL max_connections = 500;
SET GLOBAL innodb_log_file_size = 268435456; -- 256MB
SET GLOBAL innodb_flush_log_at_trx_commit = 2; -- Улучшение производительности записи
SET GLOBAL innodb_flush_method = O_DIRECT; -- Прямой доступ к диску

-- Уменьшение deadlocks (для MySQL 8.0+)
SET GLOBAL transaction_isolation = 'READ-COMMITTED';

-- Вывод подтверждения
SELECT 'Database omnichannel_crm initialized successfully!' AS status;
